import { useMemo, useRef } from 'react'
import type { Resident, ResidentAlert } from '../../types/dashboard'
import { AlertRow } from '../dashboard/AlertRow'
import { describeAlert } from '../dashboard/useAlertAnnouncer'
import { DetailSection } from './DetailSection'

interface AlertHistoryProps {
  alerts: ResidentAlert[]
  resident: Resident
  now: Date
  /** Nurse only: acknowledge and resolve. Left out for doctors, who review. */
  actions?: {
    onAcknowledge: (alertId: string) => void
    onResolve: (alertId: string) => void
  }
  announce: (message: string) => void
}

/** Every stored alert for the resident, newest first, including resolved ones (pattern 12). */
export function AlertHistory({ alerts, resident, now, actions, announce }: AlertHistoryProps) {
  const headingRef = useRef<HTMLHeadingElement>(null)
  const sorted = useMemo(() => [...alerts].sort((a, b) => b.event_ts.localeCompare(a.event_ts)), [alerts])
  const residentsById = useMemo(() => new Map([[resident.resident_id, resident]]), [resident])

  return (
    <DetailSection
      title={`Alert history (${sorted.length})`}
      description="Newest first. Resolved alerts are kept for 30 days."
      headingRef={headingRef}
    >
      {sorted.length === 0 ? (
        <p className="text-base text-slate-600">No alerts for this resident in the last 30 days.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-slate-200">
          {sorted.map((alert) => {
            const described = describeAlert(alert, residentsById)
            return (
              <AlertRow
                key={alert.alert_id}
                alert={alert}
                resident={resident}
                now={now}
                showResident={false}
                actions={
                  actions && alert.status !== 'resolved'
                    ? {
                        onAcknowledge: () => {
                          actions.onAcknowledge(alert.alert_id)
                          announce(`Acknowledged: ${described}.`)
                        },
                        onResolve: () => {
                          actions.onResolve(alert.alert_id)
                          announce(`Resolved: ${described}.`)
                          // The buttons go away; move focus somewhere stable.
                          headingRef.current?.focus()
                        },
                      }
                    : undefined
                }
              />
            )
          })}
        </ul>
      )}
    </DetailSection>
  )
}
