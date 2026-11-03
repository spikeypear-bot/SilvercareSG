/**
 * Data loaders for the dashboards and the resident page. Today they read synthetic demo
 * data with a simulated delay so loading and error states are real. Later, replace the
 * bodies of `loadDashboardData` and `loadResidentDetail` with calls through
 * src/lib/api.ts, the alert actions with the acknowledge/resolve call
 * (docs/data-model.md pattern 16), and `updateThresholds` with the thresholds call
 * (pattern 24, proposed), and `loadDeviceFleet` with the devices call (patterns 4, 5, 8);
 * the hooks and the views stay the same.
 */
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import type { FleetDevice } from '../../types/devices'
import type { ResidentDetail, VitalsThresholds } from '../../types/resident'
import {
  FAMILY_LINKED_RESIDENT_IDS,
  createDemoData,
  createDeviceFleet,
  createResidentDetail,
} from './demoData'

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

/** Every device, assigned or not, with its newest status (staff only). */
export async function loadDeviceFleet(): Promise<FleetDevice[]> {
  await delay(DEMO_DELAY_MS)
  return createDeviceFleet(new Date())
}

export type DashboardLoader = (view: DashboardView) => Promise<DashboardData>
export type ResidentDetailLoader = (residentId: string) => Promise<ResidentDetail | null>
export type DeviceFleetLoader = () => Promise<FleetDevice[]>

interface AlertActionsState {
  /**
   * Marks an open alert acknowledged. Local state only for now. `by` is the signed-in
   * staff member's display name, used only to show the change on screen: the API call
   * sends just the alert ID and action, and the server takes the actor from the JWT
   * `sub`, stores the sub, and ignores any actor sent by the client.
   */
  acknowledgeAlert: (alertId: string, by: string) => void
  /** Marks an unresolved alert resolved. `by` is for display only, as for `acknowledgeAlert`. */
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
   * server writes that entry in the same transaction (pattern 25, proposed).
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

function acknowledged(alert: ResidentAlert, by: string): ResidentAlert | null {
  return alert.status === 'open'
    ? { ...alert, status: 'acknowledged', acknowledged_by: by, acknowledged_at: new Date().toISOString() }
    : null
}

function resolved(alert: ResidentAlert, by: string): ResidentAlert | null {
  return alert.status === 'resolved'
    ? null
    : { ...alert, status: 'resolved', resolved_by: by, resolved_at: new Date().toISOString() }
}

/** Acknowledge and resolve for any state that holds an `alerts` list. */
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
    (alertId: string, by: string) => updateAlert(alertId, (a) => acknowledged(a, by)),
    [updateAlert],
  )
  const resolveAlert = useCallback(
    (alertId: string, by: string) => updateAlert(alertId, (a) => resolved(a, by)),
    [updateAlert],
  )

  return { acknowledgeAlert, resolveAlert }
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
