import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { Activity, CircleX, Droplet, HeartPulse, Thermometer, UserCheck } from 'lucide-react'
import { Button } from '../../components/Button'
import { Avatar } from '../../components/Avatar'
import { Card } from '../../components/Card'
import { EmptyState } from '../../components/EmptyState'
import { Notice } from '../../components/Notice'
import { RelativeTime } from '../../components/RelativeTime'
import { Skeleton } from '../../components/Skeleton'
import { StatusBadge } from '../../components/StatusBadge'
import { formatClockTime, formatExactTime } from '../../lib/format'
import { useNow } from '../../lib/useNow'
import type { DashboardData, Resident, ResidentAlert, VitalsReading } from '../../types/dashboard'
import { useAuth } from '../auth/useAuth'
import { AlertAnnouncer } from './AlertAnnouncer'
import {
  AcknowledgedLines,
  CancelledLine,
  LifecycleLabel,
  ResolvedLine,
  RoutingLine,
} from './AlertRow'
import { BatteryLevel, BedroomStatusText, WearableStatusText } from './DeviceStatus'
import { DashboardError, LoadingStatus } from './DashboardStates'
import {
  ALERT_TYPE_LABELS,
  formatActivity,
  formatHr,
  formatSpo2,
  formatTemp,
} from './labels'
import { MainLayout } from './MainLayout'
import { Sparkline } from './Sparkline'
import { isDeviceProblem, isOnOpenList } from './stats'
import { describeAlert, useAlertAnnouncer } from './useAlertAnnouncer'
import { useDashboardData } from './useDashboardData'
import { useEscalationWatcher } from './useEscalationWatcher'

const MAX_RECENT_ALERTS = 5

/**
 * The caregiver view (`/` for the `caregiver` group): the residents linked to the account.
 * A caregiver can cancel an open or acknowledged alert, after confirming
 * (docs/features/alert-actions-ui.md); local state only until the API exists. Device
 * problems have no cancel: the caregiver acknowledges an open one instead (user's
 * decisions 2026-11-05, docs/data-model.md pattern 16).
 */
export function CaregiverView() {
  const { data, loading, error, retry, cancelAlert, acknowledgeDeviceProblem, actionError, dismissActionError } =
    useDashboardData('family')

  let content: ReactNode
  if (error) {
    content = <DashboardError onRetry={retry} />
  } else if (loading || !data) {
    content = <CaregiverSkeleton />
  } else if (data.residents.length === 0) {
    content = (
      <EmptyState title="No residents are linked to your account yet. Your care provider can link you." />
    )
  } else {
    content = (
      <CaregiverContent data={data} onCancel={cancelAlert} onAcknowledge={acknowledgeDeviceProblem} />
    )
  }

  return (
    <MainLayout title="My residents">
      {actionError && (
        <Notice
          tone="error"
          action={
            <Button variant="secondary" onClick={dismissActionError}>
              Dismiss
            </Button>
          }
        >
          {actionError}
        </Notice>
      )}
      {content}
    </MainLayout>
  )
}

function CaregiverContent({
  data,
  onCancel,
  onAcknowledge,
}: {
  data: DashboardData
  onCancel: (alertId: string, by: string) => Promise<boolean>
  /** Acknowledges an open device problem; `by` is display only, never sent. */
  onAcknowledge: (alertId: string, by: string) => Promise<boolean>
}) {
  const { user } = useAuth()
  // Display only, never sent: the API takes who cancelled from the token.
  const caregiverName = user?.name ?? ''
  const residentsById = useMemo(
    () => new Map(data.residents.map((r) => [r.resident_id, r])),
    [data.residents],
  )
  // The page's one screen-reader announcer, fed from the alert data and the cancels.
  const { message, announce } = useAlertAnnouncer(data.alerts, residentsById)
  useEscalationWatcher(data.alerts, (alert) =>
    announce(`Escalated to the care centre nurse: ${describeAlert(alert, residentsById)}.`),
  )

  return (
    <>
      <AlertAnnouncer message={message} />
      <ul className="grid gap-6 lg:grid-cols-2">
        {data.residents.map((resident) => (
          <li key={resident.resident_id}>
            <ResidentOverview
              resident={resident}
              alerts={data.alerts.filter((a) => a.resident_id === resident.resident_id)}
              // Announced only once accepted (with API data, after the 202).
              onCancel={(alert) => {
                void onCancel(alert.alert_id, caregiverName).then((ok) => {
                  if (ok) announce(`Alert cancelled: ${describeAlert(alert, residentsById)}.`)
                })
              }}
              onAcknowledge={(alert) => {
                void onAcknowledge(alert.alert_id, caregiverName).then((ok) => {
                  if (ok) announce(`Acknowledged: ${describeAlert(alert, residentsById)}.`)
                })
              }}
            />
          </li>
        ))}
      </ul>
    </>
  )
}

// ---------------------------------------------------------------------------

interface VitalDef {
  key: 'hr_bpm' | 'spo2_pct' | 'skin_temp_c' | 'activity_pct'
  label: string
  icon: ReactNode
  format: (value: number | null) => string
}

const VITALS: readonly VitalDef[] = [
  {
    key: 'hr_bpm',
    label: 'Heart rate',
    icon: <HeartPulse className="size-5" aria-hidden="true" />,
    format: formatHr,
  },
  {
    key: 'spo2_pct',
    label: 'SpO2',
    icon: <Droplet className="size-5" aria-hidden="true" />,
    format: formatSpo2,
  },
  {
    key: 'skin_temp_c',
    label: 'Skin temperature',
    icon: <Thermometer className="size-5" aria-hidden="true" />,
    format: formatTemp,
  },
  {
    key: 'activity_pct',
    label: 'Activity',
    icon: <Activity className="size-5" aria-hidden="true" />,
    format: formatActivity,
  },
]

function ResidentOverview({
  resident,
  alerts,
  onCancel,
  onAcknowledge,
}: {
  resident: Resident
  alerts: ResidentAlert[]
  onCancel: (alert: ResidentAlert) => void
  /** Acknowledges one of this resident's open device problems. */
  onAcknowledge: (alert: ResidentAlert) => void
}) {
  const headingId = useId()
  const recentHeadingRef = useRef<HTMLHeadingElement>(null)
  // vitals are newest first; sparklines read oldest to newest.
  const oldestFirst = [...resident.vitals].reverse()
  const latest = resident.vitals[0]
  const recentAlerts = [...alerts]
    .sort((a, b) => b.event_ts.localeCompare(a.event_ts))
    .slice(0, MAX_RECENT_ALERTS)
  const now = useNow()

  return (
    <Card className="h-full p-5 sm:p-6">
      <article aria-labelledby={headingId} className="flex flex-col gap-6">
        <header className="flex flex-wrap items-center gap-4">
          <Avatar name={resident.name} size="lg" />
          <div className="min-w-0 flex-1">
            <h2 id={headingId} className="text-xl font-semibold text-slate-900">
              {resident.name}
            </h2>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <StatusBadge tone={resident.status} />
              <span className="text-base text-slate-700">{resident.status_text}</span>
            </div>
          </div>
        </header>

        <div>
          <h3 className="sr-only">Latest readings</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            {VITALS.map((vital) => (
              <VitalTile key={vital.key} vital={vital} readings={oldestFirst} latest={latest} />
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-base font-semibold text-slate-900">
            Devices
          </h3>
          <dl className="grid gap-3 text-base sm:grid-cols-2">
            <div className="rounded-xl bg-slate-50 p-3">
              <dt className="text-sm text-slate-600">Wearable</dt>
              <dd className="mt-1 flex flex-col gap-1 text-slate-900">
                <BatteryLevel wearable={resident.wearable} />
                <WearableStatusText wearable={resident.wearable} />
              </dd>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <dt className="text-sm text-slate-600">Bedroom sensor</dt>
              <dd className="mt-1 text-slate-900">
                <BedroomStatusText bedroom={resident.bedroom} />
              </dd>
            </div>
          </dl>
        </div>

        <div className="flex flex-col gap-2">
          <h3
            ref={recentHeadingRef}
            tabIndex={-1}
            className="rounded text-base font-semibold text-slate-900 focus-visible:ring-4 focus-visible:ring-teal-600/40 focus-visible:outline-none"
          >
            Recent alerts
          </h3>
          {/* Not a live region: the page's one announcer reads out new alerts and cancels. */}
          <div>
            {recentAlerts.length === 0 ? (
              <p className="text-base text-slate-600">No recent alerts.</p>
            ) : (
              <ol className="flex flex-col gap-3 border-l-2 border-slate-200 pl-4">
                {recentAlerts.map((alert) => (
                  <RecentAlertItem
                    key={alert.alert_id}
                    alert={alert}
                    resident={resident}
                    now={now}
                    onCancel={() => {
                      onCancel(alert)
                      // The item's buttons go away; move focus somewhere stable.
                      recentHeadingRef.current?.focus()
                    }}
                    onAcknowledge={() => {
                      onAcknowledge(alert)
                      // The Acknowledge button goes away, as after a cancel.
                      recentHeadingRef.current?.focus()
                    }}
                  />
                ))}
              </ol>
            )}
          </div>
        </div>
      </article>
    </Card>
  )
}

/**
 * One alert in a resident's "Recent alerts": severity, type, lifecycle, time, the lines
 * for its state, and, while it is open or acknowledged, "Cancel alert" with a
 * confirmation step (docs/features/alert-actions-ui.md, criteria 10-14). A device problem
 * has no cancel; while it is open it has "Acknowledge" instead (user's decisions 2026-11-05).
 */
function RecentAlertItem({
  alert,
  resident,
  now,
  onCancel,
  onAcknowledge,
}: {
  alert: ResidentAlert
  resident: Resident
  now: Date
  onCancel: () => void
  onAcknowledge: () => void
}) {
  const promptId = useId()
  const cancelRef = useRef<HTMLButtonElement>(null)
  const keepRef = useRef<HTMLButtonElement>(null)
  const [confirming, setConfirming] = useState(false)
  const returnFocusToCancel = useRef(false)
  const typeLabel = ALERT_TYPE_LABELS[alert.alert_type]
  const context = `${typeLabel}, ${resident.name}`
  // Main's cancel rule: open or acknowledged (docs/data-model.md, Status updates), health
  // alerts only (pattern 16).
  const canCancel = isOnOpenList(alert) && !isDeviceProblem(alert)
  const canAcknowledge = alert.status === 'open' && isDeviceProblem(alert)

  useEffect(() => {
    if (confirming) {
      // The safe choice gets focus.
      keepRef.current?.focus()
    } else if (returnFocusToCancel.current) {
      returnFocusToCancel.current = false
      cancelRef.current?.focus()
    }
  }, [confirming])

  return (
    <li className="relative flex flex-col gap-1">
      <span
        aria-hidden="true"
        className="absolute top-2 -left-[1.4rem] size-2.5 rounded-full bg-slate-400"
      />
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge tone={alert.severity} />
        <span className="text-base font-medium text-slate-900">{typeLabel}</span>
        <LifecycleLabel alert={alert} />
      </div>
      <span className="text-sm text-slate-600">
        <RelativeTime iso={alert.event_ts} now={now} />
      </span>
      {/* Explains the routing to the caregiver, who isn't the nurse; before a warning
          escalates, how long they have to cancel it. */}
      <RoutingLine alert={alert} audience="caregiver" />
      <AcknowledgedLines alert={alert} now={now} />
      <CancelledLine alert={alert} now={now} />
      <ResolvedLine alert={alert} now={now} />
      {canAcknowledge && (
        <div className="pt-1">
          <Button variant="secondary" onClick={onAcknowledge}>
            <UserCheck className="size-5" aria-hidden="true" />
            Acknowledge<span className="sr-only">: {context}</span>
          </Button>
        </div>
      )}
      {canCancel && !confirming && (
        <div className="pt-1">
          <Button ref={cancelRef} variant="secondary" onClick={() => setConfirming(true)}>
            <CircleX className="size-5" aria-hidden="true" />
            Cancel alert<span className="sr-only">: {context}</span>
          </Button>
        </div>
      )}
      {canCancel && confirming && (
        <div
          role="group"
          aria-labelledby={promptId}
          className="mt-1 flex flex-col gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3"
        >
          <p id={promptId} className="text-base text-slate-900">
            Cancel this alert? It will no longer be escalated to the care centre nurse. This
            resident's caregivers, and the care centre nurse if they were alerted, will be
            emailed that you cancelled it.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="danger" onClick={onCancel}>
              Yes, cancel alert<span className="sr-only">: {context}</span>
            </Button>
            <Button
              ref={keepRef}
              variant="secondary"
              onClick={() => {
                returnFocusToCancel.current = true
                setConfirming(false)
              }}
            >
              Keep alert<span className="sr-only">: {context}</span>
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}

function VitalTile({
  vital,
  readings,
  latest,
}: {
  vital: VitalDef
  readings: VitalsReading[]
  latest: VitalsReading | undefined
}) {
  const values = readings.map((r) => r[vital.key])
  const latestValue = latest ? latest[vital.key] : null
  const trendText = values.map((v) => vital.format(v)).join(', ')

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-slate-200 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
        <span className="text-teal-700">{vital.icon}</span>
        {vital.label}
      </div>
      <div className="flex items-end justify-between gap-2">
        <p className="text-2xl font-bold tracking-tight text-slate-900">{vital.format(latestValue)}</p>
        <Sparkline values={values} />
      </div>
      <p className="sr-only">Last {values.length} readings, oldest first: {trendText}.</p>
      {latest && (
        <p className="text-sm text-slate-600">
          At{' '}
          <time dateTime={latest.ts} title={formatExactTime(latest.ts)}>
            {formatClockTime(latest.ts)}
          </time>
        </p>
      )}
    </div>
  )
}

function CaregiverSkeleton() {
  return (
    <div aria-busy="true" className="grid gap-6 lg:grid-cols-2">
      <LoadingStatus label="Loading your residents' information" />
      {[0, 1].map((i) => (
        <Card key={i} className="flex flex-col gap-5 p-6">
          <div className="flex items-center gap-4">
            <Skeleton className="size-14 rounded-full" />
            <div className="flex flex-1 flex-col gap-2">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-6 w-56" />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {[0, 1, 2, 3].map((j) => (
              <Skeleton key={j} className="h-28 w-full rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-20 w-full rounded-xl" />
        </Card>
      ))}
    </div>
  )
}
