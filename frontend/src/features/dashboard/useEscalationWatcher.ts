import { useEffect, useRef } from 'react'
import type { ResidentAlert } from '../../types/dashboard'

/**
 * Calls `onEscalated` once for each open alert whose `escalates_at` passes while the
 * dashboard is open (not for alerts that had already escalated). Runs at dashboard level,
 * from the data, so it fires whichever panel is showing.
 */
export function useEscalationWatcher(
  alerts: ResidentAlert[],
  onEscalated: (alert: ResidentAlert) => void,
): void {
  const onEscalatedRef = useRef(onEscalated)
  useEffect(() => {
    onEscalatedRef.current = onEscalated
  }, [onEscalated])

  useEffect(() => {
    const now = Date.now()
    const timers = alerts.flatMap((alert) => {
      const at = alert.status === 'open' && alert.escalates_at ? Date.parse(alert.escalates_at) : NaN
      return at > now ? [setTimeout(() => onEscalatedRef.current(alert), at - now)] : []
    })
    return () => timers.forEach(clearTimeout)
  }, [alerts])
}
