/**
 * Main-page data loader. Today it reads synthetic demo data with a simulated delay so
 * loading and error states are real. Later, replace the body of `loadDashboardData`
 * with calls through src/lib/api.ts, and the alert actions with the acknowledge/resolve
 * call (docs/data-model.md pattern 16); the hook and the views stay the same.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import { FAMILY_LINKED_RESIDENT_IDS, createDemoData } from './demoData'

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

export type DashboardLoader = (view: DashboardView) => Promise<DashboardData>

export interface DashboardDataState {
  data: DashboardData | null
  loading: boolean
  error: boolean
  /** Loads again after an error. */
  retry: () => void
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

export function useDashboardData(
  view: DashboardView,
  loader: DashboardLoader = loadDashboardData,
): DashboardDataState {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const loaderRef = useRef(loader)
  useEffect(() => {
    loaderRef.current = loader
  }, [loader])

  useEffect(() => {
    let cancelled = false
    loaderRef
      .current(view)
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
  }, [view, attempt])

  const retry = useCallback(() => {
    setError(false)
    setLoading(true)
    setAttempt((a) => a + 1)
  }, [])

  const updateAlert = useCallback(
    (alertId: string, change: (alert: ResidentAlert) => ResidentAlert | null) => {
      setData((current) =>
        current && {
          ...current,
          alerts: current.alerts.map((a) => (a.alert_id === alertId ? (change(a) ?? a) : a)),
        },
      )
    },
    [],
  )

  const acknowledgeAlert = useCallback(
    (alertId: string, by: string) => {
      updateAlert(alertId, (a) =>
        a.status === 'open'
          ? { ...a, status: 'acknowledged', acknowledged_by: by, acknowledged_at: new Date().toISOString() }
          : null,
      )
    },
    [updateAlert],
  )

  const resolveAlert = useCallback(
    (alertId: string, by: string) => {
      updateAlert(alertId, (a) =>
        a.status === 'resolved'
          ? null
          : { ...a, status: 'resolved', resolved_by: by, resolved_at: new Date().toISOString() },
      )
    },
    [updateAlert],
  )

  return { data, loading, error, retry, acknowledgeAlert, resolveAlert }
}
