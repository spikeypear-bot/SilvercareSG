import { useCallback, useState } from 'react'
import type { Resident, ResidentAlert } from '../../types/dashboard'
import { ALERT_TYPE_LABELS } from './labels'

const NO_BREAK_SPACE = String.fromCharCode(0xa0)

/** "Fall detected, Test Resident C" */
export function describeAlert(alert: ResidentAlert, residentsById: Map<string, Resident>): string {
  const name = residentsById.get(alert.resident_id)?.name ?? 'Unknown resident'
  return `${ALERT_TYPE_LABELS[alert.alert_type]}, ${name}`
}

function newAlertsMessage(fresh: ResidentAlert[], residentsById: Map<string, Resident>): string {
  if (fresh.length === 1) {
    const [alert] = fresh
    const prefix = alert.severity === 'critical' ? 'New critical alert' : 'New alert'
    return `${prefix}: ${describeAlert(alert, residentsById)}.`
  }
  const critical = fresh.filter((a) => a.severity === 'critical').length
  return `${fresh.length} new alerts, ${critical} critical.`
}

/**
 * Screen-reader announcements for the alert lists. Announces alerts that appear after the
 * first load (e.g. when polling arrives), plus anything passed to `announce` (actions,
 * escalations). The lists themselves are not live regions, so ticking times and
 * countdowns are not read out.
 */
export function useAlertAnnouncer(
  alerts: ResidentAlert[],
  residentsById: Map<string, Resident>,
): { message: string; announce: (message: string) => void } {
  // `count` alternates a trailing no-break space, so repeating the same text still
  // changes the region and screen readers read it again.
  const [{ text, count }, setState] = useState({ text: '', count: 0 })
  const [previous, setPrevious] = useState(alerts)
  const announce = useCallback((next: string) => {
    setState((s) => ({ text: next, count: s.count + 1 }))
  }, [])

  // Compare with the previous render's alerts (React's "adjust state on prop change").
  if (alerts !== previous) {
    const seen = new Set(previous.map((a) => a.alert_id))
    const fresh = alerts.filter((a) => !seen.has(a.alert_id) && a.status !== 'resolved')
    setPrevious(alerts)
    if (fresh.length > 0) announce(newAlertsMessage(fresh, residentsById))
  }

  return { message: count % 2 ? text + NO_BREAK_SPACE : text, announce }
}
