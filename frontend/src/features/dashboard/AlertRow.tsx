import { useRef } from 'react'
import { Bell, CircleArrowUp, CircleCheckBig, Timer, UserCheck, type LucideIcon } from 'lucide-react'
import { Button } from '../../components/Button'
import { RelativeTime } from '../../components/RelativeTime'
import { StatusBadge } from '../../components/StatusBadge'
import { cx } from '../../lib/cx'
import { useNow } from '../../lib/useNow'
import type { Resident, ResidentAlert } from '../../types/dashboard'
import { ALERT_TYPE_LABELS } from './labels'
import { ResidentLine } from './ResidentLine'

export interface AlertActions {
  onAcknowledge: () => void
  onResolve: () => void
}

interface AlertRowProps {
  alert: ResidentAlert
  resident: Resident | undefined
  now: Date
  /** Acknowledge / Resolve buttons. Left out where the role reviews rather than dispatches. */
  actions?: AlertActions
  /** `list`: divided rows in a list. `tile`: a bordered box, for a grid. */
  variant?: 'list' | 'tile'
  /** The resident line. Left out on the resident's own page. */
  showResident?: boolean
}

/** One alert: severity, type, resident, home, time, lifecycle, and optional actions. */
export function AlertRow({
  alert,
  resident,
  now,
  actions,
  variant = 'list',
  showResident = true,
}: AlertRowProps) {
  const resolveRef = useRef<HTMLButtonElement>(null)
  const typeLabel = ALERT_TYPE_LABELS[alert.alert_type]
  const context = `${typeLabel}, ${resident?.name ?? 'Unknown resident'}`

  function acknowledge() {
    actions?.onAcknowledge()
    // The Acknowledge button goes away; keep focus in the row.
    resolveRef.current?.focus()
  }

  return (
    <li
      className={cx(
        'flex flex-col gap-2',
        variant === 'list' ? 'py-4 first:pt-0 last:pb-0' : 'rounded-xl border border-slate-200 p-4',
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge tone={alert.severity} />
          <LifecycleLabel alert={alert} />
        </div>
        <span className="text-sm text-slate-600">
          <RelativeTime iso={alert.event_ts} now={now} />
        </span>
      </div>
      <p className="text-base font-semibold text-slate-900">{typeLabel}</p>
      {showResident && <ResidentLine resident={resident} residentId={alert.resident_id} />}
      {alert.status === 'open' && alert.escalates_at && (
        <EscalationStatus escalatesAt={alert.escalates_at} />
      )}
      {alert.acknowledged_by && alert.acknowledged_at && (
        <p className="text-base text-slate-700">
          Acknowledged by {alert.acknowledged_by},{' '}
          <RelativeTime iso={alert.acknowledged_at} now={now} />
        </p>
      )}
      {alert.status === 'resolved' && alert.resolved_by && alert.resolved_at && (
        <p className="text-base text-slate-700">
          Resolved by {alert.resolved_by}, <RelativeTime iso={alert.resolved_at} now={now} />
        </p>
      )}
      {actions && (
        <div className="flex flex-wrap gap-2 pt-1">
          {alert.status === 'open' && (
            <Button variant="secondary" onClick={acknowledge}>
              <UserCheck className="size-5" aria-hidden="true" />
              Acknowledge<span className="sr-only">: {context}</span>
            </Button>
          )}
          <Button ref={resolveRef} variant="secondary" onClick={actions.onResolve}>
            <CircleCheckBig className="size-5" aria-hidden="true" />
            Resolve<span className="sr-only">: {context}</span>
          </Button>
        </div>
      )}
    </li>
  )
}

const LIFECYCLE: Record<ResidentAlert['status'], { label: string; Icon: LucideIcon }> = {
  open: { label: 'Open', Icon: Bell },
  acknowledged: { label: 'Acknowledged', Icon: UserCheck },
  resolved: { label: 'Resolved', Icon: CircleCheckBig },
}

function LifecycleLabel({ alert }: { alert: ResidentAlert }) {
  const { label, Icon } = LIFECYCLE[alert.status]
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-sm font-medium text-slate-800">
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      {label}
    </span>
  )
}

/**
 * Seconds left in the confirmation window, ticking every second, or "Escalated to nurses"
 * once it has passed. Not a live region: screen readers hear about escalation once,
 * through the dashboard's announcer (`useEscalationWatcher`), instead of every tick.
 */
function EscalationStatus({ escalatesAt }: { escalatesAt: string }) {
  const now = useNow(1000)
  const remainingS = Math.ceil((Date.parse(escalatesAt) - now.getTime()) / 1000)

  if (remainingS <= 0) {
    return (
      <p className="inline-flex items-center gap-1.5 text-base font-semibold text-rose-800">
        <CircleArrowUp className="size-5 shrink-0" aria-hidden="true" />
        Escalated to nurses
      </p>
    )
  }
  return (
    <p className="inline-flex items-center gap-1.5 text-base font-semibold text-amber-900">
      <Timer className="size-5 shrink-0" aria-hidden="true" />
      <span role="timer">
        Escalates to nurses in <span className="tabular-nums">{remainingS}</span> s
      </span>
    </p>
  )
}
