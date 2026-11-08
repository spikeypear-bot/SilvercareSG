import { useEffect, useRef, useState } from 'react'
import type { Resident } from '../../types/dashboard'
import { notificationText, useNotifications } from '../notifications/notifications'
import { diffAlerts, knownAlerts, type AlertChange, type AlertLike } from './alertChanges'
import { ALERT_TYPE_LABELS } from './labels'
import { useAnnouncer } from './useAnnouncer'

/** "Fall detected, Test Resident C" */
export function describeAlert(alert: Pick<AlertLike, 'alert_type' | 'resident_id'>, residentsById: Map<string, Resident>): string {
  const name = residentsById.get(alert.resident_id)?.name ?? 'Unknown resident'
  return `${ALERT_TYPE_LABELS[alert.alert_type]}, ${name}`
}

function newAlertsMessage<A extends AlertLike>(fresh: A[], describe: (alert: A) => string): string {
  if (fresh.length === 1) {
    const [alert] = fresh
    const prefix = alert.severity === 'critical' ? 'New critical alert' : 'New alert'
    return `${prefix}: ${describe(alert)}.`
  }
  const critical = fresh.filter((a) => a.severity === 'critical').length
  return `${fresh.length} new alerts, ${critical} critical.`
}

export interface AlertAnnouncerOptions<A extends AlertLike> {
  /**
   * The list holds only open and acknowledged alerts (`GET /dashboard`, the ops view), so
   * an alert that leaves it was closed. Off for lists of every status (caregiver view,
   * resident page), where the status says what happened.
   */
  openListOnly?: boolean
  /** How to name an alert; default `describeAlert` with `residentsById`. */
  describe?: (alert: A) => string
  /** True for alerts this user acted on: the bell doesn't report their own actions. */
  isOwnAction?: (alertId: string) => boolean
}

/**
 * Screen-reader announcements for the alert lists, and the notification bell's feed.
 * Compares each new list with every alert seen since the page loaded (`diffAlerts`), so
 * each new alert is announced once, however many polls show it. Announces new alerts,
 * plus anything passed to `announce` (actions, escalations); reports new alerts,
 * acknowledgements, resolutions, and cancels to the bell, which scopes them to the role.
 * The lists themselves are not live regions, so ticking times and countdowns are not
 * read out.
 */
export function useAlertAnnouncer<A extends AlertLike>(
  alerts: A[],
  residentsById: Map<string, Resident>,
  options: AlertAnnouncerOptions<A> = {},
): { message: string; announce: (message: string) => void } {
  const { openListOnly = false, describe, isOwnAction } = options
  const { message, announce } = useAnnouncer()
  const notifications = useNotifications()
  const [tracked, setTracked] = useState(() => ({ alerts, known: knownAlerts(alerts) }))
  const [batch, setBatch] = useState<AlertChange<A>[]>([])
  const describeOne = describe ?? ((alert: A) => describeAlert(alert, residentsById))

  // Compare with what was seen before (React's "adjust state on prop change").
  if (alerts !== tracked.alerts) {
    const { changes, known } = diffAlerts(tracked.known, alerts, openListOnly)
    setTracked({ alerts, known })
    const fresh = changes.filter((c) => c.kind === 'new').map((c) => c.alert)
    if (fresh.length > 0) announce(newAlertsMessage(fresh, describeOne))
    if (changes.length > 0) setBatch(changes)
  }

  const latest = useRef({ notifications, describeOne, isOwnAction, residentsById })
  useEffect(() => {
    latest.current = { notifications, describeOne, isOwnAction, residentsById }
  })

  useEffect(() => {
    const { notifications: target, describeOne: name, isOwnAction: own, residentsById: byId } = latest.current
    if (!target || batch.length === 0) return
    const inputs = batch
      .filter((c) => c.kind === 'new' || !own?.(c.alert.alert_id))
      .map((change) => ({ change, text: notificationText(change.kind, change.alert, name(change.alert)) }))
    target.report(inputs, new Set(byId.keys()))
  }, [batch])

  return { message, announce }
}
