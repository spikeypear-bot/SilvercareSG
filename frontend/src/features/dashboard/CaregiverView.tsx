import { useId, useRef, type ReactNode } from 'react'
import { Activity, Droplet, HeartPulse, Thermometer, UserCheck } from 'lucide-react'
import { Avatar } from '../../components/Avatar'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { EmptyState } from '../../components/EmptyState'
import { RelativeTime } from '../../components/RelativeTime'
import { Skeleton } from '../../components/Skeleton'
import { StatusBadge } from '../../components/StatusBadge'
import { formatClockTime, formatExactTime } from '../../lib/format'
import { useNow } from '../../lib/useNow'
import type { Resident, ResidentAlert, VitalsReading } from '../../types/dashboard'
import { AcknowledgedLines, EscalatedText } from './AlertRow'
import { BatteryLevel, BedroomStatusText, WearableStatusText } from './DeviceStatus'
import { DashboardError, LoadingStatus } from './DashboardStates'
import {
  ALERT_TYPE_LABELS,
  formatActivity,
  formatHr,
  formatSpo2,
  formatTemp,
} from './labels'
import { useAuth } from '../auth/useAuth'
import { MainLayout } from './MainLayout'
import { Sparkline } from './Sparkline'
import { isDeviceProblem, sentToNurseAtOnce } from './stats'
import { useDashboardData } from './useDashboardData'

const MAX_RECENT_ALERTS = 5

/**
 * The caregiver view (`/` for the `caregiver` group): the residents linked to the account.
 * The caregiver can acknowledge an open device problem (user's decision 2026-11-05); health
 * alerts are display only here (the nurse acknowledges them; the caregiver Cancel button
 * is Track B's).
 */
export function CaregiverView() {
  const { data, loading, error, retry, acknowledgeDeviceProblem } = useDashboardData('family')
  const { user } = useAuth()
  const caregiverName = user?.name || 'Caregiver'

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
      <ul className="grid gap-6 lg:grid-cols-2">
        {data.residents.map((resident) => (
          <li key={resident.resident_id}>
            <ResidentOverview
              resident={resident}
              alerts={data.alerts.filter((a) => a.resident_id === resident.resident_id)}
              onAcknowledge={(alertId) => acknowledgeDeviceProblem(alertId, caregiverName)}
            />
          </li>
        ))}
      </ul>
    )
  }

  return <MainLayout title="My residents">{content}</MainLayout>
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

interface ResidentOverviewProps {
  resident: Resident
  alerts: ResidentAlert[]
  /** Acknowledges one of this resident's open device problems. */
  onAcknowledge: (alertId: string) => void
}

function ResidentOverview({ resident, alerts, onAcknowledge }: ResidentOverviewProps) {
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
          {/* Takes focus after an acknowledge: the button goes away. */}
          <h3 ref={recentHeadingRef} tabIndex={-1} className="text-base font-semibold text-slate-900">
            Recent alerts
          </h3>
          <div aria-live="polite">
            {recentAlerts.length === 0 ? (
              <p className="text-base text-slate-600">No recent alerts.</p>
            ) : (
              <ol className="flex flex-col gap-3 border-l-2 border-slate-200 pl-4">
                {recentAlerts.map((alert) => (
                  <li key={alert.alert_id} className="relative flex flex-col gap-1">
                    <span
                      aria-hidden="true"
                      className="absolute top-2 -left-[1.4rem] size-2.5 rounded-full bg-slate-400"
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge tone={alert.severity} />
                      <span className="text-base font-medium text-slate-900">
                        {ALERT_TYPE_LABELS[alert.alert_type]}
                      </span>
                    </div>
                    <span className="text-sm text-slate-600">
                      <RelativeTime iso={alert.event_ts} now={now} />
                    </span>
                    {/* Explains the routing to the caregiver, who isn't the nurse. */}
                    {alert.status === 'open' && alert.escalated_at && sentToNurseAtOnce(alert) && (
                      <EscalatedText atOnce />
                    )}
                    <AcknowledgedLines alert={alert} now={now} />
                    {alert.status === 'open' && isDeviceProblem(alert) && (
                      <div className="pt-1">
                        <Button
                          variant="secondary"
                          onClick={() => {
                            onAcknowledge(alert.alert_id)
                            recentHeadingRef.current?.focus()
                          }}
                        >
                          <UserCheck className="size-5" aria-hidden="true" />
                          Acknowledge
                          <span className="sr-only">
                            : {ALERT_TYPE_LABELS[alert.alert_type]}, {resident.name}
                          </span>
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      </article>
    </Card>
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
