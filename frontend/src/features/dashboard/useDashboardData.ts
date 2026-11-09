/**
 * Data loaders for the dashboards, the resident page, and the devices view.
 * `loadDeviceFleet` (`GET /devices`) and the staff view of `loadDashboardData`
 * (`GET /dashboard`) call the API through src/lib/api.ts. The caregiver view and
 * `loadResidentDetail` (`GET /residents/{id}` and `GET .../thresholds/history`) call the
 * API too, and `updateThresholds` sends `PUT .../thresholds` (pattern 26). Acknowledge,
 * cancel, and resolve go to the API too (docs/data-model.md pattern 16).
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type Dispatch,
  type SetStateAction,
} from 'react'
import { useRefreshHeld } from '../../lib/refreshHold'
import { STAFF_CHANNELS, residentChannels, useLiveRefresh } from '../../lib/live'
import type { DeviceProblem } from '../../types/ops'
import type {
  ApiDashboardAlert,
  ApiDashboardResident,
  DashboardData,
  Resident,
  ResidentAlert,
} from '../../types/dashboard'
import {
  ApiError,
  acknowledgeAlertApi,
  cancelAlertApi,
  getDashboard,
  getDeviceProblems,
  getDevices,
  getMyResidents,
  getResident,
  getThresholdHistory,
  putThresholds,
  resolveAlertApi,
} from '../../lib/api'
import type { ApiDevice, FleetDevice } from '../../types/devices'
import { parseAlertLimits } from '../devices/alertLimits'
import type {
  ApiLinkedResident,
  ApiResidentAlert,
  ApiResidentDetail,
  ApiThresholdChange,
  ResidentDetail,
  ResidentThresholds,
  ThresholdChangeEntry,
  VitalsThresholds,
} from '../../types/resident'
import { isDeviceProblem, isOnOpenList } from './stats'

export type DashboardView = 'staff' | 'family'

/**
 * How often the staff dashboards, the devices view, and the ops dashboard poll
 * (docs/data-model.md: patterns 5, 8, and 9 every 30 s per open dashboard). Polling
 * pauses while the tab is hidden and while a form is open (`lib/refreshHold.ts`).
 */
export const POLL_MS = 30_000
/** A tab coming back into view or focus refetches, unless the last load is newer than this. */
export const FOCUS_REFETCH_MIN_GAP_MS = 5_000
/**
 * How long an action's on-screen change is kept while the server still returns the old
 * status. Actions are applied asynchronously after the 202 (pattern 25), usually within
 * seconds; after this the server's state wins (e.g. alert-processor refused it).
 */
export const OVERRIDE_TTL_MS = 2 * 60_000

/** The demo modules' simulated delay (demoData.ts; the loaders now call the API). */
export const DEMO_DELAY_MS = 400

/**
 * One `GET /dashboard` resident as the dashboards show it. Copies each field by name, so
 * a field the API adds later isn't passed through. `display_name` becomes `name`; a
 * missing `home_id` becomes ''.
 */
export function toResident(r: ApiDashboardResident): Resident {
  const w = r.wearable
  const b = r.bedroom
  return {
    resident_id: r.resident_id,
    name: r.display_name,
    home_id: r.home_id ?? '',
    status: r.status,
    status_text: r.status_text,
    wearable: w && {
      device_id: w.device_id,
      ts: w.ts,
      battery_pct: w.battery_pct,
      worn: w.worn,
      charging: w.charging,
      online: w.online,
    },
    bedroom: b && { device_id: b.device_id, ts: b.ts, online: b.online },
    vitals: r.vitals.map((v) => ({
      ts: v.ts,
      hr_bpm: v.hr_bpm,
      spo2_pct: v.spo2_pct,
      skin_temp_c: v.skin_temp_c,
      // Not sent by GET /dashboard (data minimisation).
      activity_pct: null,
    })),
  }
}

/**
 * One `GET /dashboard` alert as the dashboards show it. The API sends null for an absent
 * value; the views expect the optional field to be missing, so nulls are dropped.
 */
export function toResidentAlert(a: ApiDashboardAlert | ApiResidentAlert): ResidentAlert {
  return {
    alert_id: a.alert_id,
    resident_id: a.resident_id,
    device_id: a.device_id,
    alert_type: a.alert_type,
    severity: a.severity,
    status: a.status,
    event_ts: a.event_ts,
    ...(a.acknowledged_at !== null && { acknowledged_at: a.acknowledged_at }),
    ...(a.acknowledged_by_role !== null && { acknowledged_by_role: a.acknowledged_by_role }),
    ...(a.acknowledged_by !== null && { acknowledged_by: a.acknowledged_by }),
    ...(a.ack_note !== null && { ack_note: a.ack_note }),
    ...(a.escalated_at !== null && { escalated_at: a.escalated_at }),
    ...(a.escalates_at !== null && { escalates_at: a.escalates_at }),
  }
}

/** A `GET /me/residents` or resident-page resident: as `toResident`, keeping `activity_pct`. */
export function toLinkedResident(r: ApiLinkedResident): Resident {
  return {
    ...toResident(r),
    vitals: r.vitals.map((v) => ({
      ts: v.ts,
      hr_bpm: v.hr_bpm,
      spo2_pct: v.spo2_pct,
      skin_temp_c: v.skin_temp_c,
      activity_pct: v.activity_pct,
    })),
  }
}

/** A stored alert in any status (pattern 12): `toResidentAlert` plus the resolve and cancel fields. */
export function toHistoryAlert(a: ApiResidentAlert): ResidentAlert {
  return {
    ...toResidentAlert(a),
    ...(a.resolved_at !== null && { resolved_at: a.resolved_at }),
    ...(a.resolved_by !== null && { resolved_by: a.resolved_by }),
    ...(a.cancelled_at !== null && { cancelled_at: a.cancelled_at }),
    ...(a.cancelled_by_role !== null && { cancelled_by_role: a.cancelled_by_role }),
    ...(a.cancelled_by !== null && { cancelled_by: a.cancelled_by }),
  }
}

/** `GET /residents/{id}` as the resident page shows it, copied field by field. */
export function toResidentDetail(d: ApiResidentDetail): ResidentDetail {
  const w = d.wearable
  const b = d.bedroom
  const t = d.thresholds
  return {
    resident: toLinkedResident(d.resident),
    wearable: w && {
      device_id: w.device_id,
      device_type: 'wearable',
      online: w.online,
      latest: {
        vitals: w.latest.vitals.map((e) => ({
          message_id: e.message_id,
          ts: e.ts,
          hr_bpm: e.hr_bpm,
          spo2_pct: e.spo2_pct,
          skin_temp_c: e.skin_temp_c,
          activity_pct: e.activity_pct,
        })),
        fall: w.latest.fall.map((e) => ({
          message_id: e.message_id,
          ts: e.ts,
          confidence: e.confidence,
          peak_g: e.peak_g,
          still_s: e.still_s,
        })),
        status: w.latest.status.map((e) => ({
          message_id: e.message_id,
          ts: e.ts,
          battery_pct: e.battery_pct,
          worn: e.worn,
          charging: e.charging,
          fw_version: e.fw_version,
        })),
      },
    },
    bedroom: b && {
      device_id: b.device_id,
      device_type: 'bedroom',
      online: b.online,
      latest: {
        motion: b.latest.motion.map((e) => ({ message_id: e.message_id, ts: e.ts, motion: e.motion })),
        bed: b.latest.bed.map((e) => ({ message_id: e.message_id, ts: e.ts, occupied: e.occupied })),
        status: b.latest.status.map((e) => ({
          message_id: e.message_id,
          ts: e.ts,
          battery_pct: e.battery_pct,
          fw_version: e.fw_version,
        })),
      },
    },
    alerts: d.alerts.map(toHistoryAlert),
    thresholds: toThresholds(t),
    // From GET .../thresholds/history (loadResidentDetail), not this response.
    threshold_history: [],
  }
}

/** API thresholds (resident page or save response), copied field by field. */
export function toThresholds(t: ApiResidentDetail['thresholds']): ResidentThresholds {
  return {
    hr_bpm: { min: t.hr_bpm.min, max: t.hr_bpm.max },
    spo2_pct: { min: t.spo2_pct.min },
    skin_temp_c: { min: t.skin_temp_c.min, max: t.skin_temp_c.max },
    ...(t.updated_at !== null && { updated_at: t.updated_at }),
    ...(t.updated_by !== null && { updated_by: t.updated_by }),
  }
}

/** One change-history entry: the API's `changed_by_name` is shown as `changed_by`. */
export function toThresholdChange(c: ApiThresholdChange): ThresholdChangeEntry {
  return {
    change_id: c.change_id,
    changed_at: c.changed_at,
    changed_by: c.changed_by_name,
    changes: c.changes.map((x) => ({ field: x.field, old_value: x.old_value, new_value: x.new_value })),
  }
}

/**
 * Staff: every resident and the open list from `GET /dashboard`.
 * Family (caregiver): the linked residents and all their alerts from `GET /me/residents`.
 */
export async function loadDashboardData(view: DashboardView): Promise<DashboardData> {
  if (view === 'staff') {
    const body = await getDashboard()
    return {
      residents: body.residents.map(toResident),
      alerts: body.alerts.map(toResidentAlert),
      // Nurse only; copied by field name like the rest, so nothing else passes through.
      ...(body.homes && {
        homes: body.homes.map((h) => ({ home_id: h.home_id, address: h.address, lat: h.lat, lng: h.lng })),
      }),
      ...(body.homes_error && { homes_error: true }),
    }
  }
  const body = await getMyResidents()
  return { residents: body.residents.map(toLinkedResident), alerts: body.alerts.map(toHistoryAlert) }
}

/**
 * One resident's page (patterns 10, 11, 12, 18) and its threshold change history
 * (pattern 27). `null` if there is no such resident (404).
 */
export async function loadResidentDetail(residentId: string): Promise<ResidentDetail | null> {
  try {
    const [detail, history] = await Promise.all([getResident(residentId), getThresholdHistory(residentId)])
    return { ...toResidentDetail(detail), threshold_history: history.map(toThresholdChange) }
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

/**
 * One `GET /devices` device as the devices view shows it. Copies each field by name, so
 * a field the API adds later isn't passed through. `display_name` becomes `name` (the
 * resident ID if the resident has no profile); a missing `home_id` becomes ''.
 * `alert_limits` is checked (invalid limits become null) and is null for bedroom sensors.
 */
export function toFleetDevice(device: ApiDevice): FleetDevice {
  const a = device.assigned_to
  return {
    device_id: device.device_id,
    device_type: device.device_type,
    assigned_to: a && { resident_id: a.resident_id, name: a.display_name ?? a.resident_id, home_id: a.home_id ?? '' },
    online: device.online,
    last_seen: device.last_seen,
    battery_pct: device.battery_pct,
    charging: device.charging,
    fw_version: device.fw_version,
    alert_limits: device.device_type === 'wearable' ? parseAlertLimits(device.alert_limits) : null,
  }
}

/** Every device, assigned or not, with its newest status (staff only): `GET /devices`. */
export async function loadDeviceFleet(): Promise<FleetDevice[]> {
  const devices = await getDevices()
  return devices.map(toFleetDevice)
}

export type DashboardLoader = (view: DashboardView) => Promise<DashboardData>
export type ResidentDetailLoader = (residentId: string) => Promise<ResidentDetail | null>
export type DeviceFleetLoader = () => Promise<FleetDevice[]>

/**
 * Acknowledge, cancel, and the caregiver's device-problem acknowledge resolve to `true`
 * once the change is shown: with API data, after the API accepted it (202); with demo
 * data, at once. `false` if nothing changed (not allowed, already pending, or the API
 * refused it: then `actionError` says why). Announce only on `true`.
 */
interface AlertActionsState {
  /**
   * Acknowledges an open **health** alert (a device problem is left unchanged: nurses
   * don't acknowledge those, user's decisions 2026-11-05), with the nurse's optional note
   * (already normalised by `checkAckNote`; `undefined` for none). No name: an
   * acknowledge is shown as "the care centre nurse".
   *
   * With API data (the staff view): `POST /residents/{resident_id}/alerts/{alert_id}/acknowledge`
   * with `{"event_ts", "note"}` taken from the alert this hook holds; the server takes the
   * actor from the token. The alert shows as acknowledged once the API accepts it (202);
   * on an error it stays as it was and `actionError` is set. With demo data: local only.
   */
  acknowledgeAlert: (alertId: string, ackNote?: string) => Promise<boolean>
  /**
   * Cancels an open or acknowledged health alert as a caregiver. `by` is the caregiver's
   * display name, used only to show the change on screen and never sent.
   *
   * With API data: `POST /residents/{resident_id}/alerts/{alert_id}/cancel` with
   * `{"event_ts"}`; the server takes the actor (`sub`, `name`) from the token and checks
   * the `caregiver` group and the link. Shown once accepted, as for acknowledge. With
   * demo data (the caregiver view and resident page today): local only.
   */
  cancelAlert: (alertId: string, by: string) => Promise<boolean>
  /**
   * A caregiver's acknowledge of an open **device problem** (a health alert is left
   * unchanged). Local state only for now. `by` is the caregiver's display name, used only
   * to show the change on screen: the API call sends just the alert ID and action, and
   * the server takes the actor (`sub`, and `name` for the email) from the ID token and
   * checks the `caregiver` group and the link to the resident.
   */
  acknowledgeDeviceProblem: (alertId: string, by: string) => Promise<boolean>
  /**
   * Resolves an open or acknowledged **health** alert (a device problem is left
   * unchanged: only the watchdog resolves those, user's decisions 2026-11-05) with the
   * required closing note (already normalised by `checkResolveNote`). `by` is the
   * signed-in staff member's display name, used only to show the change on screen and
   * never sent.
   *
   * With API data: `POST /residents/{resident_id}/alerts/{alert_id}/resolve` with
   * `{"event_ts", "note"}` (docs/data-model.md, Track B route: resolve); the server takes
   * the actor from the token and checks the `nurse` or `doctor` group, the type, and the
   * status. Shown once accepted (202), as for acknowledge; on an error the alert stays as
   * it was and `actionError` is set. With demo data: local only.
   */
  resolveAlert: (alertId: string, by: string, resolveNote: string) => Promise<boolean>
}

export interface DashboardDataState extends AlertActionsState {
  data: DashboardData | null
  /** A sentence about the last acknowledge or cancel the API refused; null if none. */
  actionError: string | null
  dismissActionError: () => void
  loading: boolean
  error: boolean
  /** Loads again after an error. */
  retry: () => void
  /** The last background refresh failed: the data shown is from an earlier load. */
  refreshFailed: boolean
  /** Whether this view polls (only then does a failed refresh retry by itself). */
  polling: boolean
}

export interface ResidentDetailState extends AlertActionsState {
  data: ResidentDetail | null
  /** A sentence about the last acknowledge the API refused; null if none. */
  actionError: string | null
  dismissActionError: () => void
  loading: boolean
  error: boolean
  /** The last background refresh failed: the data shown is from an earlier load. */
  refreshFailed: boolean
  /** Loaded, but there is no resident with that ID. */
  notFound: boolean
  retry: () => void
  /**
   * Replaces the resident's thresholds. Resolves to null once saved, or to a sentence
   * saying why not (nothing changed on screen then).
   *
   * With API data: `PUT /residents/{resident_id}/thresholds` with just the values; the
   * server checks the `doctor` group, validates them with the same rules as the form,
   * records the token's `sub` and Cognito name, and writes the change-history entry in
   * the same transaction. The thresholds shown are the ones the server returns, and the
   * history is fetched again so the new entry appears (if that refetch fails, the save
   * still counts and `actionError` says to reload). `by` is not sent.
   *
   * With demo data (a test's own loader): local only, shown as changed by `by`; the
   * history is left as it is.
   */
  updateThresholds: (values: VitalsThresholds, by: string) => Promise<string | null>
}

function subscribeVisibility(listener: () => void) {
  document.addEventListener('visibilitychange', listener)
  return () => document.removeEventListener('visibilitychange', listener)
}

function isPageVisible() {
  return document.visibilityState !== 'hidden'
}

interface LoadOptions<T> {
  /** Refetch every this many ms while the tab is visible and no form is open; 0 never. */
  pollMs?: number
  /** Called with every result (first load and refreshes) just before it is shown. */
  onLoaded?: (result: T) => void
}

/**
 * Loads `key` with `loader`, and again when `key` changes or after `retry`. Once loaded,
 * `refresh` reloads in the background (no loading state; a failure keeps the data and
 * sets `refreshFailed`). It also runs every `pollMs` while the tab is visible, when the
 * tab comes back into view or focus, and never while a form holds refreshes: a refresh
 * asked for with `queue` during a hold or another load runs once that ends.
 */
function useLoaded<K, T>(key: K, loader: (key: K) => Promise<T>, { pollMs = 0, onLoaded }: LoadOptions<T> = {}) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [loadedKey, setLoadedKey] = useState(key)
  const loaderRef = useRef(loader)
  const onLoadedRef = useRef(onLoaded)
  const keyRef = useRef(key)
  const hasDataRef = useRef(false)
  const lastLoadAt = useRef(0)
  const inFlight = useRef(false)
  const queued = useRef(false)
  const held = useRefreshHeld()
  const heldRef = useRef(held)
  const visible = useSyncExternalStore(subscribeVisibility, isPageVisible, isPageVisible)
  const hasData = data !== null
  useEffect(() => {
    loaderRef.current = loader
    onLoadedRef.current = onLoaded
    keyRef.current = key
    hasDataRef.current = hasData
    heldRef.current = held
  })

  // A new key (e.g. another resident) shows the loading state, not the old data.
  if (key !== loadedKey) {
    setLoadedKey(key)
    setData(null)
    setError(false)
    setRefreshFailed(false)
    setLoading(true)
  }

  useEffect(() => {
    let cancelled = false
    loaderRef
      .current(key)
      .then((result) => {
        if (cancelled) return
        onLoadedRef.current?.(result)
        lastLoadAt.current = Date.now()
        setData(result)
        setLoading(false)
      })
      .catch(() => {
        if (cancelled) return
        setError(true)
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [key, attempt])

  const refreshRef = useRef<(queue?: boolean) => void>(() => {})
  const refresh = useCallback((queue = false) => {
    // Before the first load, or after it failed, `retry` loads instead.
    if (!hasDataRef.current) return
    if (inFlight.current || heldRef.current) {
      if (queue) queued.current = true
      return
    }
    inFlight.current = true
    const forKey = keyRef.current
    loaderRef
      .current(forKey)
      .then(
        (result) => {
          if (keyRef.current !== forKey) return
          onLoadedRef.current?.(result)
          lastLoadAt.current = Date.now()
          setData(result)
          setRefreshFailed(false)
        },
        () => {
          if (keyRef.current === forKey) setRefreshFailed(true)
        },
      )
      .finally(() => {
        inFlight.current = false
        if (queued.current && !heldRef.current) {
          queued.current = false
          refreshRef.current()
        }
      })
  }, [])
  useEffect(() => {
    refreshRef.current = refresh
  }, [refresh])

  // Poll while visible, loaded, and not held.
  useEffect(() => {
    if (!pollMs || !hasData || held || !visible) return
    const id = setInterval(() => refreshRef.current(), pollMs)
    return () => clearInterval(id)
  }, [pollMs, hasData, held, visible])

  // When a hold ends: what was queued, or a poll that came due meanwhile.
  useEffect(() => {
    if (held) return
    const due = pollMs > 0 && Date.now() - lastLoadAt.current >= pollMs
    if (queued.current || due) {
      queued.current = false
      refreshRef.current()
    }
  }, [held, pollMs])

  // Back to this tab or window: refetch, unless the data is only seconds old.
  useEffect(() => {
    function onReturn() {
      if (document.visibilityState === 'hidden') return
      if (Date.now() - lastLoadAt.current >= FOCUS_REFETCH_MIN_GAP_MS) refreshRef.current()
    }
    window.addEventListener('focus', onReturn)
    document.addEventListener('visibilitychange', onReturn)
    return () => {
      window.removeEventListener('focus', onReturn)
      document.removeEventListener('visibilitychange', onReturn)
    }
  }, [])

  const retry = useCallback(() => {
    setError(false)
    setRefreshFailed(false)
    setLoading(true)
    setAttempt((a) => a + 1)
  }, [])

  return { data, setData, loading, error, retry, refresh, refreshFailed }
}

/**
 * An action's change, shown while the server still returns one of `bases`: the statuses
 * the alert had before this session's actions on it (e.g. `open` and `acknowledged` for
 * an acknowledge then a resolve, both still being applied).
 */
interface AlertOverride<A> {
  bases: ReadonlySet<string | null>
  alert: A
  at: number
}

type Overrides<A> = ReadonlyMap<string, AlertOverride<A>>

/**
 * Drops the overrides the server has caught up with (its status is none of the ones the
 * actions started from), and those older than `OVERRIDE_TTL_MS`. An override for an
 * alert the server no longer returns is kept only if the action closed it (resolve,
 * cancel), so this session's resolved alerts stay listed as before; an acknowledged
 * one that left the open list was closed by someone else.
 */
export function pruneOverrides<A extends { alert_id: string; status: string | null }>(
  overrides: Overrides<A>,
  server: readonly A[],
  now: number,
): Overrides<A> {
  if (overrides.size === 0) return overrides
  const byId = new Map(server.map((a) => [a.alert_id, a]))
  const next = new Map<string, AlertOverride<A>>()
  for (const [id, o] of overrides) {
    const current = byId.get(id)
    const keep = current
      ? o.bases.has(current.status) && now - o.at < OVERRIDE_TTL_MS
      : !isOnOpenList(o.alert)
    if (keep) next.set(id, o)
  }
  return next.size === overrides.size ? overrides : next
}

/** The server's alerts with this session's pending changes on top. */
export function applyOverrides<A extends { alert_id: string; status: string | null }>(
  server: A[],
  overrides: Overrides<A>,
): A[] {
  if (overrides.size === 0) return server
  const ids = new Set(server.map((a) => a.alert_id))
  const merged = server.map((a) => {
    const o = overrides.get(a.alert_id)
    return o && o.bases.has(a.status) ? o.alert : a
  })
  for (const [id, o] of overrides) if (!ids.has(id)) merged.push(o.alert)
  return merged
}

function useAlertOverrides<A extends { alert_id: string; status: string | null }>() {
  const [overrides, setOverrides] = useState<Overrides<A>>(() => new Map())
  // Alerts this user changed in this session: the bell doesn't report their own actions.
  const own = useRef(new Set<string>())
  const setOverride = useCallback((before: A, after: A) => {
    own.current.add(before.alert_id)
    setOverrides((prev) => {
      const bases = new Set(prev.get(before.alert_id)?.bases)
      bases.add(before.status)
      return new Map(prev).set(before.alert_id, { bases, alert: after, at: Date.now() })
    })
  }, [])
  const prune = useCallback((server: readonly A[]) => {
    setOverrides((prev) => pruneOverrides(prev, server, Date.now()))
  }, [])
  const isOwnAction = useCallback((alertId: string) => own.current.has(alertId), [])
  return { overrides, setOverride, prune, isOwnAction }
}

function acknowledged(alert: ResidentAlert, ackNote: string | undefined): ResidentAlert | null {
  return alert.status === 'open' && !isDeviceProblem(alert)
    ? {
        ...alert,
        status: 'acknowledged',
        acknowledged_at: new Date().toISOString(),
        ...(ackNote && { ack_note: ackNote }),
      }
    : null
}

/**
 * Main's cancel rule: from `open` or `acknowledged`; keeps the acknowledge and its note.
 * Health alerts only: device problems have no cancel (docs/data-model.md, pattern 16).
 */
function cancelled(alert: ResidentAlert, by: string): ResidentAlert | null {
  return (alert.status === 'open' || alert.status === 'acknowledged') && !isDeviceProblem(alert)
    ? {
        ...alert,
        status: 'cancelled',
        cancelled_at: new Date().toISOString(),
        cancelled_by_role: 'caregiver',
        ...(by && { cancelled_by: by }),
      }
    : null
}

function acknowledgedByCaregiver(alert: ResidentAlert, by: string): ResidentAlert | null {
  return alert.status === 'open' && isDeviceProblem(alert)
    ? {
        ...alert,
        status: 'acknowledged',
        acknowledged_at: new Date().toISOString(),
        acknowledged_by_role: 'caregiver',
        ...(by && { acknowledged_by: by }),
      }
    : null
}

function resolved(alert: ResidentAlert, by: string): ResidentAlert | null {
  // A cancelled alert is already off the open list and expires like a resolved one. Only
  // the watchdog resolves a device problem.
  return alert.status === 'resolved' || alert.status === 'cancelled' || isDeviceProblem(alert)
    ? null
    : { ...alert, status: 'resolved', resolved_by: by, resolved_at: new Date().toISOString() }
}

/** What to tell the doctor when the API refuses a thresholds save. */
export function thresholdsErrorText(err: unknown): string {
  const status = err instanceof ApiError ? err.status : 0
  if (status === 409) {
    return 'Someone else changed these thresholds just now. Reload the page to see their values, then try again.'
  }
  if (status === 404) return 'This resident has no thresholds to change.'
  if (status === 403) return 'Only doctors can change thresholds.'
  if (status === 401) return 'Your session has ended. Sign in again.'
  if (status === 400) return 'The thresholds were not accepted. Check the values and try again.'
  return 'Could not save the thresholds. Check your connection and try again.'
}

/** What to tell the user when the API refuses an acknowledge, cancel, or resolve. */
export function alertActionErrorText(err: unknown, action: 'acknowledge' | 'cancel' | 'resolve'): string {
  const status = err instanceof ApiError ? err.status : 0
  // A note the form accepted but the API refused (the rules should match, so rare).
  if (status === 400 && action !== 'cancel') return 'The note was not accepted. Check it and try again.'
  if (status === 409) return 'This alert has already been dealt with. Reload the page to see its current state.'
  if (status === 404) return 'This alert could not be found. Reload the page to see the current alerts.'
  if (status === 403) return `You can't ${action} this alert.`
  if (status === 401) return 'Your session has ended. Sign in again.'
  return `Could not ${action} the alert. Check your connection and try again.`
}

/**
 * Acknowledge, resolve, and cancel for any state that holds an `alerts` list. With
 * `live`, each goes through the API first and is shown only once accepted (202), then
 * the view refetches (`refresh`). The change is kept on screen as an override until a
 * load returns the new status: the API applies it asynchronously, so a refetch right
 * after the 202 can still return the old one.
 */
function useAlertActions(
  alerts: readonly ResidentAlert[] | null,
  setOverride: (before: ResidentAlert, after: ResidentAlert) => void,
  refresh: (queue?: boolean) => void,
  live = false,
): AlertActionsState & {
  actionError: string | null
  dismissActionError: () => void
  setActionError: Dispatch<SetStateAction<string | null>>
} {
  const alertsRef = useRef(alerts)
  useEffect(() => {
    alertsRef.current = alerts
  }, [alerts])
  // Alerts with a request in flight: a second click doesn't send a second request.
  const pending = useRef(new Set<string>())
  const [actionError, setActionError] = useState<string | null>(null)
  const dismissActionError = useCallback(() => setActionError(null), [])

  /**
   * Applies `change` to the alert: at once without `live`; with `live`, only after
   * `request` succeeds, then refetches. Resolves to whether it was applied.
   */
  const apply = useCallback(
    (
      alertId: string,
      action: 'acknowledge' | 'cancel' | 'resolve',
      change: (alert: ResidentAlert) => ResidentAlert | null,
      request: (alert: ResidentAlert) => Promise<unknown>,
    ): Promise<boolean> => {
      const alert = alertsRef.current?.find((a) => a.alert_id === alertId)
      const changed = alert && change(alert)
      if (!alert || !changed) return Promise.resolve(false)
      if (!live) {
        setOverride(alert, changed)
        return Promise.resolve(true)
      }
      if (pending.current.has(alertId)) return Promise.resolve(false)
      pending.current.add(alertId)
      setActionError(null)
      return request(alert)
        .then(
          () => {
            setOverride(alert, changed)
            refresh(true)
            return true
          },
          (err: unknown) => {
            setActionError(alertActionErrorText(err, action))
            return false
          },
        )
        .finally(() => pending.current.delete(alertId))
    },
    [live, setOverride, refresh],
  )

  const acknowledgeAlert = useCallback(
    (alertId: string, ackNote?: string) =>
      apply(alertId, 'acknowledge', (a) => acknowledged(a, ackNote), (a) => acknowledgeAlertApi(a, ackNote ?? null)),
    [apply],
  )
  const cancelAlert = useCallback(
    (alertId: string, by: string) => apply(alertId, 'cancel', (a) => cancelled(a, by), (a) => cancelAlertApi(a)),
    [apply],
  )
  // `by` is display only: the API takes the caregiver's name from the token.
  const acknowledgeDeviceProblem = useCallback(
    (alertId: string, by: string) =>
      apply(alertId, 'acknowledge', (a) => acknowledgedByCaregiver(a, by), (a) => acknowledgeAlertApi(a, null)),
    [apply],
  )
  const resolveAlert = useCallback(
    (alertId: string, by: string, resolveNote: string) =>
      apply(alertId, 'resolve', (a) => resolved(a, by), (a) => resolveAlertApi(a, resolveNote)),
    [apply],
  )

  return {
    acknowledgeAlert,
    resolveAlert,
    cancelAlert,
    acknowledgeDeviceProblem,
    actionError,
    dismissActionError,
    setActionError,
  }
}

/** `data` with `overrides` applied to its alerts (the same object when there are none). */
function useWithOverrides<T extends { alerts: ResidentAlert[] }>(
  data: T | null,
  overrides: Overrides<ResidentAlert>,
): T | null {
  return useMemo(
    () => (data && overrides.size > 0 ? { ...data, alerts: applyOverrides(data.alerts, overrides) } : data),
    [data, overrides],
  )
}

/**
 * The nurse and doctor dashboards poll every 30 s (data-model patterns 5, 8, 9). The
 * caregiver view refetches only on focus and after an action: `GET /me/residents` is
 * throttled to one call per 5 s for all caregivers together (infra/api.tf) and reads
 * about 5 RCU per linked resident, mostly from the latest table ingest also reads, and
 * the data model sizes it per page view (patterns 10-14).
 */
export function useDashboardData(
  view: DashboardView,
  loader: DashboardLoader = loadDashboardData,
): DashboardDataState & { isOwnAction: (alertId: string) => boolean } {
  const { overrides, setOverride, prune, isOwnAction } = useAlertOverrides<ResidentAlert>()
  const pollMs = view === 'staff' ? POLL_MS : 0
  const onLoaded = useCallback((d: DashboardData) => prune(d.alerts), [prune])
  const { data: server, loading, error, retry, refresh, refreshFailed } = useLoaded(view, loader, { pollMs, onLoaded })
  const data = useWithOverrides(server, overrides)
  // Actions go to the API only when the alerts came from it (the default loader); a
  // test's own loader serves demo alerts whose IDs the API would refuse.
  const live = loader === loadDashboardData
  // Live alerts (live-alerts criterion 19): staff hear every resident, a caregiver their
  // linked residents (the IDs this view already loaded). Only with API data.
  const residentIds = server?.residents.map((r) => r.resident_id).join(',') ?? ''
  const liveChannels = useMemo(() => {
    if (!live) return []
    if (view === 'staff') return STAFF_CHANNELS
    return residentIds === '' ? [] : residentIds.split(',').flatMap(residentChannels)
  }, [live, view, residentIds])
  useLiveRefresh(liveChannels, () => refresh(true))
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- not part of this state
  const { setActionError, ...actions } = useAlertActions(data?.alerts ?? null, setOverride, refresh, live)
  return { data, loading, error, retry, refreshFailed, polling: pollMs > 0, isOwnAction, ...actions }
}

/**
 * The resident page isn't polled (patterns 10-12 are per page view, and its route is
 * throttled for that, infra/api.tf): it refetches on focus and after an action, never
 * while a threshold or note form is open.
 */
export function useResidentDetail(
  residentId: string,
  loader: ResidentDetailLoader = loadResidentDetail,
): ResidentDetailState & { isOwnAction: (alertId: string) => boolean } {
  const { overrides, setOverride, prune, isOwnAction } = useAlertOverrides<ResidentAlert>()
  const onLoaded = useCallback((d: ResidentDetail | null) => prune(d?.alerts ?? []), [prune])
  const {
    data: server,
    setData,
    loading,
    error,
    retry,
    refresh,
    refreshFailed,
  } = useLoaded(residentId, loader, { onLoaded })
  const data = useWithOverrides(server, overrides)
  // As useDashboardData: the API only for data from the API loader.
  const live = loader === loadResidentDetail
  const liveChannels = useMemo(() => (live ? residentChannels(residentId) : []), [live, residentId])
  useLiveRefresh(liveChannels, () => refresh(true))
  const { setActionError, ...actions } = useAlertActions(data?.alerts ?? null, setOverride, refresh, live)
  const saving = useRef(false)

  const updateThresholds = useCallback(
    async (values: VitalsThresholds, by: string): Promise<string | null> => {
      // Copy only the limits: a caller passing a full ResidentThresholds can't carry its
      // own updated_by / updated_at through. putThresholds sends exactly these too.
      const { hr_bpm, spo2_pct, skin_temp_c } = values
      if (!live) {
        setData(
          (current) =>
            current && {
              ...current,
              thresholds: {
                hr_bpm: { min: hr_bpm.min, max: hr_bpm.max },
                spo2_pct: { min: spo2_pct.min },
                skin_temp_c: { min: skin_temp_c.min, max: skin_temp_c.max },
                updated_by: by,
                updated_at: new Date().toISOString(),
              },
            },
        )
        return null
      }
      if (saving.current) return 'A save is already in progress.'
      saving.current = true
      try {
        const saved = await putThresholds(residentId, values)
        setData((current) => current && { ...current, thresholds: toThresholds(saved.thresholds) })
        try {
          const history = await getThresholdHistory(residentId)
          setData((current) => current && { ...current, threshold_history: history.map(toThresholdChange) })
        } catch {
          setActionError('The thresholds were saved, but the change history could not be refreshed. Reload the page.')
        }
        return null
      } catch (err) {
        return thresholdsErrorText(err)
      } finally {
        saving.current = false
      }
    },
    [live, residentId, setData, setActionError],
  )

  const notFound = !loading && !error && data === null
  return { data, loading, error, notFound, retry, refreshFailed, isOwnAction, updateThresholds, ...actions }
}

export interface DeviceFleetState {
  data: FleetDevice[] | null
  loading: boolean
  error: boolean
  retry: () => void
  refreshFailed: boolean
}

/** The devices view (staff and ops): read-only, no device actions; polls every 30 s. */
export function useDeviceFleet(loader: DeviceFleetLoader = loadDeviceFleet): DeviceFleetState {
  const { data, loading, error, retry, refreshFailed } = useLoaded('fleet', loader, { pollMs: POLL_MS })
  return { data, loading, error, retry, refreshFailed }
}

export type DeviceProblemsLoader = () => Promise<DeviceProblem[]>

export interface DeviceProblemsState {
  data: DeviceProblem[] | null
  loading: boolean
  error: boolean
  retry: () => void
  refreshFailed: boolean
  /** The problem with an acknowledge in flight, or null. */
  pending: string | null
  /** The last acknowledge failed. */
  actionError: boolean
  /** Acknowledges an open device problem as ops; resolves to whether the API accepted it. */
  acknowledge: (problem: DeviceProblem) => Promise<boolean>
  isOwnAction: (alertId: string) => boolean
}

/**
 * The ops dashboard: `GET /ops/device-problems`, polled every 30 s (the route allows one
 * load per 20 s, infra/api.tf). Acknowledge is shown once accepted (202), kept until the
 * server returns it, and followed by a refetch.
 */
export function useDeviceProblems(loader: DeviceProblemsLoader = getDeviceProblems): DeviceProblemsState {
  const { overrides, setOverride, prune, isOwnAction } = useAlertOverrides<DeviceProblem>()
  const { data: server, loading, error, retry, refresh, refreshFailed } = useLoaded('ops', loader, {
    pollMs: POLL_MS,
    onLoaded: prune,
  })
  const data = useMemo(() => server && applyOverrides(server, overrides), [server, overrides])
  const [pending, setPending] = useState<string | null>(null)
  const [actionError, setActionError] = useState(false)

  const acknowledge = useCallback(
    async (problem: DeviceProblem): Promise<boolean> => {
      if (problem.status !== 'open') return false
      setPending(problem.alert_id)
      setActionError(false)
      try {
        await acknowledgeAlertApi(problem, null)
        setOverride(problem, {
          ...problem,
          status: 'acknowledged',
          acknowledged_at: new Date().toISOString(),
          acknowledged_by_role: 'ops',
          acknowledged_by: 'the ops team',
        })
        refresh(true)
        return true
      } catch {
        setActionError(true)
        return false
      } finally {
        setPending(null)
      }
    },
    [setOverride, refresh],
  )

  return { data, loading, error, retry, refreshFailed, pending, actionError, acknowledge, isOwnAction }
}
