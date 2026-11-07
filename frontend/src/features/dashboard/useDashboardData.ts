/**
 * Data loaders for the dashboards, the resident page, and the devices view.
 * `loadDeviceFleet` calls the API (`GET /devices` through src/lib/api.ts). The others
 * still read synthetic demo data with a simulated delay so loading and error states are
 * real. Later, replace the
 * bodies of `loadDashboardData` and `loadResidentDetail` with calls through
 * src/lib/api.ts, the alert actions with the acknowledge / cancel / resolve calls
 * (docs/data-model.md pattern 16; docs/features/alert-actions-ui.md, API contract; nurses
 * acknowledge and resolve health alerts, caregivers acknowledge device problems), and `updateThresholds` with the thresholds call
 * (the proposed thresholds-edit pattern); the hooks and the views stay the same.
 */
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import { getDevices } from '../../lib/api'
import type { ApiDevice, FleetDevice } from '../../types/devices'
import type { ResidentDetail, VitalsThresholds } from '../../types/resident'
import {
  FAMILY_LINKED_RESIDENT_IDS,
  createDemoData,
  createResidentDetail,
} from './demoData'
import { isDeviceProblem } from './stats'

export type DashboardView = 'staff' | 'family'

export const DEMO_DELAY_MS = 400

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Staff: every demo resident and all open demo alerts.
 * Family: only the demo residents linked to the account (the same 2 for every user).
 */
export async function loadDashboardData(view: DashboardView): Promise<DashboardData> {
  await delay(DEMO_DELAY_MS)
  const data = createDemoData(new Date())
  if (view === 'staff') return data
  const linked = new Set(FAMILY_LINKED_RESIDENT_IDS)
  return {
    residents: data.residents.filter((r) => linked.has(r.resident_id)),
    alerts: data.alerts.filter((a) => linked.has(a.resident_id)),
  }
}

/** One resident's page (patterns 10, 11, 12, 18). `null` if there is no such resident. */
export async function loadResidentDetail(residentId: string): Promise<ResidentDetail | null> {
  await delay(DEMO_DELAY_MS)
  return createResidentDetail(residentId, new Date())
}

/**
 * One `GET /devices` device as the devices view shows it. Copies each field by name, so
 * a field the API adds later isn't passed through. `display_name` becomes `name` (the
 * resident ID if the resident has no profile); a missing `home_id` becomes ''.
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

interface AlertActionsState {
  /**
   * Marks an open **health** alert acknowledged (a device problem is left unchanged:
   * nurses don't acknowledge those, user's decisions 2026-11-05), with the nurse's optional note (already normalised
   * by `checkAckNote`; `undefined` for none). Local state only for now
   * (docs/features/alert-actions-ui.md). No name: an acknowledge is shown as "the care
   * centre nurse". Later this body becomes, through src/lib/api.ts:
   * `POST /residents/{resident_id}/alerts/{alert_id}/acknowledge` with body
   * `{"event_ts": <the alert's event_ts>, "note": ackNote ?? null}`, both IDs and
   * `event_ts` taken from the alert this hook holds; the server takes the actor from the
   * JWT `sub` and checks the `nurse` group.
   */
  acknowledgeAlert: (alertId: string, ackNote?: string) => void
  /**
   * Marks an open or acknowledged alert cancelled by a caregiver. Local state only for
   * now. `by` is the signed-in caregiver's display name, used only to show the change on
   * screen and never sent. Later this body becomes
   * `POST /residents/{resident_id}/alerts/{alert_id}/cancel` with body
   * `{"event_ts": <the alert's event_ts>}`; the server takes the actor (`sub`, `name`)
   * from the JWT, checks the `caregiver` group and the link to the resident.
   */
  cancelAlert: (alertId: string, by: string) => void
  /**
   * A caregiver's acknowledge of an open **device problem** (a health alert is left
   * unchanged). Local state only for now. `by` is the caregiver's display name, used only
   * to show the change on screen: the API call sends just the alert ID and action, and
   * the server takes the actor (`sub`, and `name` for the email) from the ID token and
   * checks the `caregiver` group and the link to the resident.
   */
  acknowledgeDeviceProblem: (alertId: string, by: string) => void
  /**
   * Marks an open or acknowledged **health** alert resolved (a device problem is left
   * unchanged: only the watchdog resolves those, user's decisions 2026-11-05). `by` is the
   * signed-in staff member's display name, used only to show the change on screen: the
   * API call sends just the alert ID and action, and the server takes the actor from the
   * JWT `sub`, stores the sub, and ignores any actor sent by the client. The API call
   * (docs/data-model.md, Track B route: resolve) also sends the required note; the portal
   * has no resolve note box yet.
   */
  resolveAlert: (alertId: string, by: string) => void
}

export interface DashboardDataState extends AlertActionsState {
  data: DashboardData | null
  loading: boolean
  error: boolean
  /** Loads again after an error. */
  retry: () => void
}

export interface ResidentDetailState extends AlertActionsState {
  data: ResidentDetail | null
  loading: boolean
  error: boolean
  /** Loaded, but there is no resident with that ID. */
  notFound: boolean
  retry: () => void
  /**
   * Replaces the resident's thresholds. Local state only for now. `by` is for display
   * only: the API call sends just the values, and the server checks the caller is in the
   * `doctor` group, validates the values with the same rules as the form, and records
   * the `sub` from the JWT as `updated_by`. It doesn't add to `threshold_history`: the
   * server writes that entry in the same transaction (the proposed threshold change history).
   */
  updateThresholds: (values: VitalsThresholds, by: string) => void
}

/** Loads `key` with `loader`, and again when `key` changes or after `retry`. */
function useLoaded<K, T>(key: K, loader: (key: K) => Promise<T>) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [loadedKey, setLoadedKey] = useState(key)
  const loaderRef = useRef(loader)
  useEffect(() => {
    loaderRef.current = loader
  }, [loader])

  // A new key (e.g. another resident) shows the loading state, not the old data.
  if (key !== loadedKey) {
    setLoadedKey(key)
    setData(null)
    setError(false)
    setLoading(true)
  }

  useEffect(() => {
    let cancelled = false
    loaderRef
      .current(key)
      .then((result) => {
        if (cancelled) return
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

  const retry = useCallback(() => {
    setError(false)
    setLoading(true)
    setAttempt((a) => a + 1)
  }, [])

  return { data, setData, loading, error, retry }
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

/** Acknowledge, resolve, and cancel for any state that holds an `alerts` list. */
function useAlertActions<T extends { alerts: ResidentAlert[] }>(
  setData: Dispatch<SetStateAction<T | null>>,
): AlertActionsState {
  const updateAlert = useCallback(
    (alertId: string, change: (alert: ResidentAlert) => ResidentAlert | null) => {
      setData(
        (current) =>
          current && {
            ...current,
            alerts: current.alerts.map((a) => (a.alert_id === alertId ? (change(a) ?? a) : a)),
          },
      )
    },
    [setData],
  )

  const acknowledgeAlert = useCallback(
    (alertId: string, ackNote?: string) => updateAlert(alertId, (a) => acknowledged(a, ackNote)),
    [updateAlert],
  )
  const cancelAlert = useCallback(
    (alertId: string, by: string) => updateAlert(alertId, (a) => cancelled(a, by)),
    [updateAlert],
  )
  const acknowledgeDeviceProblem = useCallback(
    (alertId: string, by: string) => updateAlert(alertId, (a) => acknowledgedByCaregiver(a, by)),
    [updateAlert],
  )
  const resolveAlert = useCallback(
    (alertId: string, by: string) => updateAlert(alertId, (a) => resolved(a, by)),
    [updateAlert],
  )

  return { acknowledgeAlert, resolveAlert, cancelAlert, acknowledgeDeviceProblem }
}

export function useDashboardData(
  view: DashboardView,
  loader: DashboardLoader = loadDashboardData,
): DashboardDataState {
  const { data, setData, loading, error, retry } = useLoaded(view, loader)
  const actions = useAlertActions(setData)
  return { data, loading, error, retry, ...actions }
}

export function useResidentDetail(
  residentId: string,
  loader: ResidentDetailLoader = loadResidentDetail,
): ResidentDetailState {
  const { data, setData, loading, error, retry } = useLoaded(residentId, loader)
  const actions = useAlertActions(setData)

  const updateThresholds = useCallback(
    (values: VitalsThresholds, by: string) => {
      // Copy only the limits: a caller passing a full ResidentThresholds can't carry its
      // own updated_by / updated_at through. The API call will send exactly these too.
      const { hr_bpm, spo2_pct, skin_temp_c } = values
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
    },
    [setData],
  )

  const notFound = !loading && !error && data === null
  return { data, loading, error, notFound, retry, updateThresholds, ...actions }
}

export interface DeviceFleetState {
  data: FleetDevice[] | null
  loading: boolean
  error: boolean
  retry: () => void
}

/** The devices view: read-only, no device actions. */
export function useDeviceFleet(loader: DeviceFleetLoader = loadDeviceFleet): DeviceFleetState {
  const { data, loading, error, retry } = useLoaded('fleet', loader)
  return { data, loading, error, retry }
}
