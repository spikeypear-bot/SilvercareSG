import type { AlertSeverity, AlertType } from '../../types/dashboard'
import { isOnOpenList } from './stats'

/**
 * What the change detection needs of an alert: a dashboard or resident-page alert
 * (`ResidentAlert`) or an ops device problem (`DeviceProblem`).
 */
export interface AlertLike {
  alert_id: string
  resident_id: string
  device_id?: string | null
  alert_type: AlertType
  severity?: AlertSeverity | null
  status: string | null
}

/**
 * - `new`: an open or acknowledged alert not seen before on this page;
 * - `acknowledged`, `resolved`, `cancelled`: the status it moved to from the open list;
 * - `closed`: it left a list that holds only open alerts (`GET /dashboard`,
 *   `GET /ops/device-problems`), so it was resolved or cancelled, and the list doesn't
 *   say which.
 */
export type AlertChangeKind = 'new' | 'acknowledged' | 'resolved' | 'cancelled' | 'closed'

export interface AlertChange<A extends AlertLike = AlertLike> {
  kind: AlertChangeKind
  alert: A
}

/** The last version of every alert seen since the page loaded, and its status then. */
export type KnownAlerts<A extends AlertLike> = ReadonlyMap<string, { alert: A; status: string | null }>

export function knownAlerts<A extends AlertLike>(alerts: readonly A[]): KnownAlerts<A> {
  return new Map(alerts.map((a) => [a.alert_id, { alert: a, status: a.status }]))
}

/**
 * Compares the alerts now shown with every alert seen since the page loaded (not only
 * the previous poll), so an alert is reported as new once, even if a poll briefly
 * leaves it out. The one comparison behind both the screen-reader announcer and the
 * notification bell.
 */
export function diffAlerts<A extends AlertLike>(
  known: KnownAlerts<A>,
  alerts: readonly A[],
  openListOnly: boolean,
): { changes: AlertChange<A>[]; known: KnownAlerts<A> } {
  const next = new Map(known)
  const changes: AlertChange<A>[] = []
  const present = new Set<string>()

  for (const alert of alerts) {
    present.add(alert.alert_id)
    const before = known.get(alert.alert_id)
    if (!before) {
      if (isOnOpenList(alert)) changes.push({ kind: 'new', alert })
    } else if (before.status !== alert.status && isOnOpenList(before)) {
      if (alert.status === 'acknowledged' && before.status === 'open') changes.push({ kind: 'acknowledged', alert })
      if (alert.status === 'resolved') changes.push({ kind: 'resolved', alert })
      if (alert.status === 'cancelled') changes.push({ kind: 'cancelled', alert })
    }
    next.set(alert.alert_id, { alert, status: alert.status })
  }

  if (openListOnly) {
    for (const [id, before] of known) {
      if (present.has(id) || !isOnOpenList(before)) continue
      changes.push({ kind: 'closed', alert: before.alert })
      next.set(id, { alert: before.alert, status: 'closed' })
    }
  }

  return { changes, known: next }
}
