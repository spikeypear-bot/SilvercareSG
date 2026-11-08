import { createContext, useContext } from 'react'
import type { AppView } from '../../types/auth'
import type { AlertChange, AlertChangeKind, AlertLike } from '../dashboard/alertChanges'
import { isDeviceProblem } from '../dashboard/stats'

/**
 * One item in the notification bell. Built from the changes the page's alert announcer
 * detects in polled data (`diffAlerts`), so the bell and the screen reader agree. Kept in
 * memory only, never in browser storage: the text can hold a resident's name.
 */
export interface AppNotification {
  /** `<kind>:<alert_id>`: one item per alert and kind, however often it is detected. */
  key: string
  kind: AlertChangeKind
  alert_id: string
  /** ISO 8601: when this page noticed it (the next poll after it happened). */
  noticed_at: string
  text: string
  seen: boolean
}

/** What a page reports: the change, and the sentence the bell shows for it. */
export interface NotificationInput {
  change: AlertChange
  text: string
}

export interface NotificationsContextValue {
  items: AppNotification[]
  unseenCount: number
  /**
   * Adds the changes this user may see (`scopeChanges`); repeats of a `key` are ignored.
   * `linkedResidentIds`: the residents the reporting page shows (the caregiver's linked
   * residents on the caregiver view).
   */
  report: (inputs: NotificationInput[], linkedResidentIds: ReadonlySet<string>) => void
  markAllSeen: () => void
  dismiss: (key: string) => void
  clearAll: () => void
}

export const NotificationsContext = createContext<NotificationsContextValue | null>(null)

/** The bell's state, or null outside `NotificationsProvider` (e.g. a component test). */
export function useNotifications(): NotificationsContextValue | null {
  return useContext(NotificationsContext)
}

/** The bell keeps at most this many items, newest first. */
export const MAX_NOTIFICATIONS = 50

/**
 * What each view may be told about, on top of what its API already returns:
 * - nurse, doctor: every change on their pages (staff see every resident);
 * - ops: device problems only, never a health alert;
 * - caregiver (`family` view): only their linked residents;
 * - anyone else: nothing.
 */
export function scopeChanges<T extends { change: AlertChange }>(
  view: AppView,
  inputs: readonly T[],
  linkedResidentIds: ReadonlySet<string>,
): T[] {
  if (view === 'nurse' || view === 'doctor') return [...inputs]
  if (view === 'ops') return inputs.filter((i) => isDeviceProblem(i.change.alert))
  if (view === 'family') return inputs.filter((i) => linkedResidentIds.has(i.change.alert.resident_id))
  return []
}

/** The bell's sentence for one change; `described` is e.g. "Fall detected, Test Resident C". */
export function notificationText(kind: AlertChangeKind, alert: AlertLike, described: string): string {
  const device = isDeviceProblem(alert)
  switch (kind) {
    case 'new':
      if (device) return `New device problem: ${described}.`
      return alert.severity === 'critical' ? `New critical alert: ${described}.` : `New alert: ${described}.`
    case 'acknowledged':
      return `Acknowledged: ${described}.`
    case 'resolved':
      return `Resolved: ${described}.`
    case 'cancelled':
      return `Cancelled: ${described}.`
    case 'closed':
      // Only the watchdog resolves device problems; a health alert may also be cancelled.
      return device ? `Resolved: ${described}.` : `Resolved or cancelled: ${described}.`
  }
}
