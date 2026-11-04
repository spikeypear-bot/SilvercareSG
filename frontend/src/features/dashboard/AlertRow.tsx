import { useEffect, useRef, useState } from 'react'
import { Bell, CircleArrowUp, CircleCheckBig, CircleX, Timer, UserCheck, type LucideIcon } from 'lucide-react'
import { Button } from '../../components/Button'
import { RelativeTime } from '../../components/RelativeTime'
import { StatusBadge } from '../../components/StatusBadge'
import { cx } from '../../lib/cx'
import { useNow } from '../../lib/useNow'
import type { Resident, ResidentAlert } from '../../types/dashboard'
import { AckNoteForm } from './AckNoteForm'
import { ALERT_TYPE_LABELS } from './labels'
import { ResidentLine } from './ResidentLine'
import { isOnOpenList, sentToNurseAtOnce } from './stats'

export interface AlertActions {
  /**
   * Acknowledges with the nurse's optional note (normalised, `undefined` for none). Called
   * only when the note form is submitted, never when it is cancelled.
   */
  onAcknowledge: (ackNote: string | undefined) => void
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
  /**
   * "Sent to the care centre nurse at once" on open critical and unassigned alerts. Left
   * out on the nurse dashboard's inbox: the nurse is the recipient, and the Critical badge
   * already says it. Shown where it explains the routing to someone else.
   */
  showSentAtOnce?: boolean
}

/** One alert: severity, type, resident, home, time, lifecycle, and optional actions. */
export function AlertRow({
  alert,
  resident,
  now,
  actions,
  variant = 'list',
  showResident = true,
  showSentAtOnce = true,
}: AlertRowProps) {
  const resolveRef = useRef<HTMLButtonElement>(null)
  const acknowledgeRef = useRef<HTMLButtonElement>(null)
  // Acknowledge opens the note form first (docs/features/alert-actions-ui.md, criterion 1).
  const [noteOpen, setNoteOpen] = useState(false)
  const returnFocusToAcknowledge = useRef(false)
  const typeLabel = ALERT_TYPE_LABELS[alert.alert_type]
  const context = `${typeLabel}, ${resident?.name ?? 'Unknown resident'}`
  const canAcknowledge = alert.status === 'open'

  useEffect(() => {
    if (!noteOpen && returnFocusToAcknowledge.current) {
      returnFocusToAcknowledge.current = false
      acknowledgeRef.current?.focus()
    }
  }, [noteOpen])

  function submitNote(ackNote: string | undefined) {
    setNoteOpen(false)
    actions?.onAcknowledge(ackNote)
    // The Acknowledge button goes away; keep focus in the row.
    resolveRef.current?.focus()
  }

  function cancelNote() {
    returnFocusToAcknowledge.current = true
    setNoteOpen(false)
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
      {alert.status === 'open' && alert.escalated_at && (sentToNurseAtOnce(alert) ? (
        showSentAtOnce && <EscalatedText atOnce />
      ) : (
        <EscalatedText atOnce={false} />
      ))}
      {alert.status === 'open' && !alert.escalated_at && alert.escalates_at && (
        <EscalationStatus escalatesAt={alert.escalates_at} />
      )}
      <AcknowledgedLines alert={alert} now={now} />
      <CancelledLine alert={alert} now={now} />
      <ResolvedLine alert={alert} now={now} />
      {actions && isOnOpenList(alert) && canAcknowledge && noteOpen && (
        <AckNoteForm context={context} onSubmit={submitNote} onCancel={cancelNote} />
      )}
      {actions && isOnOpenList(alert) && (
        <div className="flex flex-wrap gap-2 pt-1">
          {canAcknowledge && !noteOpen && (
            <Button ref={acknowledgeRef} variant="secondary" onClick={() => setNoteOpen(true)}>
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
  cancelled: { label: 'Cancelled', Icon: CircleX },
  resolved: { label: 'Resolved', Icon: CircleCheckBig },
}

/** Open / Acknowledged / Cancelled / Resolved, as an icon and text (never colour alone). */
export function LifecycleLabel({ alert }: { alert: ResidentAlert }) {
  const { label, Icon } = LIFECYCLE[alert.status]
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-sm font-medium text-slate-800">
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      {label}
    </span>
  )
}

/**
 * "Cancelled by the resident ("I'm OK"), <time>" or "Cancelled by <name> (caregiver),
 * <time>" on a cancelled alert; nothing otherwise.
 */
export function CancelledLine({ alert, now }: { alert: ResidentAlert; now: Date }) {
  if (alert.status !== 'cancelled' || !alert.cancelled_at) return null
  return (
    <p className="text-base text-slate-700">
      {alert.cancelled_by_role === 'caregiver'
        ? `Cancelled by ${alert.cancelled_by || 'a caregiver'} (caregiver), `
        : 'Cancelled by the resident ("I\'m OK"), '}
      <RelativeTime iso={alert.cancelled_at} now={now} />
    </p>
  )
}

/** "Resolved by <name>, <time>" on a resolved alert; nothing otherwise. */
export function ResolvedLine({ alert, now }: { alert: ResidentAlert; now: Date }) {
  if (alert.status !== 'resolved' || !alert.resolved_by || !alert.resolved_at) return null
  return (
    <p className="text-base text-slate-700">
      Resolved by {alert.resolved_by}, <RelativeTime iso={alert.resolved_at} now={now} />
    </p>
  )
}

/** "Sent to the care centre nurse at once" (critical, unassigned) or "Escalated to …" (a warning after 5 min). */
export function EscalatedText({ atOnce }: { atOnce: boolean }) {
  return (
    <p className="inline-flex items-center gap-1.5 text-base font-semibold text-rose-800">
      <CircleArrowUp className="size-5 shrink-0" aria-hidden="true" />
      {atOnce ? 'Sent to the care centre nurse at once' : 'Escalated to the care centre nurse'}
    </p>
  )
}

/**
 * "Acknowledged by the care centre nurse, <time>" and the nurse's note, if any. Display
 * only, for everyone who can see the alert: staff and the resident's caregivers
 * (docs/data-model.md, `ack_note`). Nothing when the alert was never acknowledged.
 */
export function AcknowledgedLines({ alert, now }: { alert: ResidentAlert; now: Date }) {
  if (!alert.acknowledged_at) return null
  return (
    <>
      <p className="text-base text-slate-700">
        Acknowledged by the care centre nurse, <RelativeTime iso={alert.acknowledged_at} now={now} />
      </p>
      {alert.ack_note && <AckNote note={alert.ack_note} />}
    </>
  )
}

/** The nurse's acknowledge note, as plain text with its line breaks (React escapes it). */
function AckNote({ note }: { note: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-base font-medium text-slate-700">Message from the care centre:</p>
      <p className="text-base break-words whitespace-pre-line text-slate-800">{note}</p>
    </div>
  )
}

/** "4:35" for 275 s. */
function minutesSeconds(totalS: number): string {
  return `${Math.floor(totalS / 60)}:${String(totalS % 60).padStart(2, '0')}`
}

/**
 * Time until the 300 s escalation check on a warning (docs/features/escalation.md,
 * alert-routing.md) as m:ss, ticking every second, or "Escalated to the care centre
 * nurse" once it has passed (the next load brings `escalated_at`). Not a live region:
 * screen readers hear about escalation once, through the page's announcer
 * (`useEscalationWatcher`), instead of every tick.
 */
function EscalationStatus({ escalatesAt }: { escalatesAt: string }) {
  const now = useNow(1000)
  const remainingS = Math.ceil((Date.parse(escalatesAt) - now.getTime()) / 1000)

  if (remainingS <= 0) return <EscalatedText atOnce={false} />
  return (
    <p className="inline-flex items-center gap-1.5 text-base font-semibold text-amber-900">
      <Timer className="size-5 shrink-0" aria-hidden="true" />
      <span role="timer">
        Escalates to the care centre nurse in <span className="tabular-nums">{minutesSeconds(remainingS)}</span>
      </span>
    </p>
  )
}
