import { useId, useState, type ReactNode } from 'react'
import { BatteryLow, Cpu, Watch, WifiOff } from 'lucide-react'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { EmptyState } from '../../components/EmptyState'
import { Notice } from '../../components/Notice'
import { RelativeTime } from '../../components/RelativeTime'
import { Skeleton } from '../../components/Skeleton'
import { useNow } from '../../lib/useNow'
import type { DeviceProblem, DeviceProblemType } from '../../types/ops'
import { AlertAnnouncer } from '../dashboard/AlertAnnouncer'
import { DashboardError, LoadingStatus, RefreshFailedNotice } from '../dashboard/DashboardStates'
import { ALERT_TYPE_LABELS } from '../dashboard/labels'
import { MainLayout } from '../dashboard/MainLayout'
import { StaffNav } from '../dashboard/StaffNav'
import { StatTile, StatTilesSkeleton } from '../dashboard/StatTile'
import { useAlertAnnouncer } from '../dashboard/useAlertAnnouncer'
import { useDeviceProblems, type DeviceProblemsState } from '../dashboard/useDashboardData'
import { describeProblem } from './describeProblem'

/**
 * The ops view (docs/data-model.md, the ops paragraph): open and acknowledged device
 * problems for every resident, with Acknowledge. No health alerts, resident names, or
 * readings: the API sends none (GET /ops/device-problems filters server-side), and
 * every resident route answers ops with 403, so residents are shown by opaque ID only.
 * Device problems are resolved by the watchdog when they clear, so there is no resolve.
 */
export function OpsDashboard() {
  const ops = useDeviceProblems()

  return (
    <MainLayout title="Ops dashboard" before={<StaffNav label="Ops area" />}>
      {ops.refreshFailed && ops.data && <RefreshFailedNotice polling />}
      {ops.error ? (
        <DashboardError title="We couldn't load the device problems." onRetry={ops.retry} />
      ) : ops.loading || ops.data === null ? (
        <OpsSkeleton />
      ) : (
        <OpsContent problems={ops.data} ops={ops} />
      )}
    </MainLayout>
  )
}

/** No resident names for ops: the announcer and the bell name devices and resident IDs. */
const NO_RESIDENTS = new Map<string, never>()

type Filter = 'all' | DeviceProblemType

const FILTERS: ReadonlyArray<{ id: Filter; label: string; spoken: string; empty: string; icon: ReactNode }> = [
  {
    id: 'all',
    label: 'All device problems',
    spoken: 'all device problems',
    empty: 'No device problems right now.',
    icon: <Cpu className="size-6" aria-hidden="true" />,
  },
  {
    id: 'device_offline',
    label: 'Offline',
    spoken: 'offline',
    empty: 'No devices are offline.',
    icon: <WifiOff className="size-6" aria-hidden="true" />,
  },
  {
    id: 'battery_low',
    label: 'Low battery',
    spoken: 'low battery',
    empty: 'No devices are low on battery.',
    icon: <BatteryLow className="size-6" aria-hidden="true" />,
  },
  {
    id: 'not_worn',
    label: 'Not worn',
    spoken: 'not worn',
    empty: 'Every wearable is being worn.',
    icon: <Watch className="size-6" aria-hidden="true" />,
  },
]

function OpsContent({ problems, ops }: { problems: DeviceProblem[]; ops: DeviceProblemsState }) {
  const [filter, setFilter] = useState<Filter>('all')
  const { pending, actionError, isOwnAction } = ops
  // The page's one announcer: new device problems (polling), filter changes, acknowledges.
  const { message, announce } = useAlertAnnouncer(problems, NO_RESIDENTS, {
    openListOnly: true,
    describe: describeProblem,
    isOwnAction,
  })

  const count = (f: Filter) => (f === 'all' ? problems.length : problems.filter((p) => p.alert_type === f).length)
  const visible = filter === 'all' ? problems : problems.filter((p) => p.alert_type === filter)
  const current = FILTERS.find((f) => f.id === filter) ?? FILTERS[0]

  function choose(next: Filter) {
    setFilter(next)
    const spec = FILTERS.find((f) => f.id === next) ?? FILTERS[0]
    announce(`Showing ${count(next)} of ${problems.length}: ${spec.spoken}.`)
  }

  function acknowledge(problem: DeviceProblem) {
    // Queued (202); alert-processor applies it. Shown at once, then refetched.
    void ops.acknowledge(problem).then((ok) => {
      if (ok) announce(`${ALERT_TYPE_LABELS[problem.alert_type]} on ${problem.device_id ?? 'the device'} acknowledged.`)
    })
  }

  return (
    <>
      <AlertAnnouncer message={message} />
      <section aria-labelledby="ops-stats-heading">
        <h2 id="ops-stats-heading" className="sr-only">
          Summary
        </h2>
        <div
          role="group"
          aria-label="Choose which device problems the list shows"
          className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
        >
          {FILTERS.map((f) => (
            <StatTile
              key={f.id}
              label={f.label}
              value={count(f.id)}
              icon={f.icon}
              selected={filter === f.id}
              onSelect={() => choose(f.id)}
            />
          ))}
        </div>
      </section>

      {actionError && (
        <Notice tone="error" title="We couldn't acknowledge that device problem.">
          It may already have been acknowledged or cleared. Reload the page and try again.
        </Notice>
      )}

      {problems.length === 0 ? (
        <EmptyState title="No device problems" description="Offline, low-battery, and not-worn devices appear here." />
      ) : (
        <ProblemList
          problems={visible}
          title={current.label}
          emptyText={current.empty}
          pending={pending}
          onAcknowledge={acknowledge}
        />
      )}
    </>
  )
}

function detailText(problem: DeviceProblem, now: Date): ReactNode {
  const d = problem.details ?? {}
  if (problem.alert_type === 'device_offline' && d.last_seen) {
    return (
      <>
        Last seen <RelativeTime iso={d.last_seen} now={now} />
      </>
    )
  }
  if (problem.alert_type === 'battery_low' && typeof d.battery_pct === 'number') {
    return `Battery ${d.battery_pct}%`
  }
  if (problem.alert_type === 'not_worn' && d.since) {
    return (
      <>
        Off the wrist since <RelativeTime iso={d.since} now={now} />
      </>
    )
  }
  return null
}

function ProblemList({
  problems,
  title,
  emptyText,
  pending,
  onAcknowledge,
}: {
  problems: DeviceProblem[]
  title: string
  emptyText: string
  pending: string | null
  onAcknowledge: (p: DeviceProblem) => void
}) {
  const headingId = useId()
  const now = useNow()
  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-4">
        <h2 id={headingId} className="text-lg font-semibold text-slate-900">
          {title} <span className="font-normal text-slate-600">({problems.length})</span>
        </h2>
        {problems.length === 0 ? (
          <p className="text-base text-slate-600">{emptyText}</p>
        ) : (
          <ul className="flex flex-col divide-y divide-slate-100">
            {problems.map((p) => (
              <li key={p.alert_id} className="flex flex-col gap-3 py-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex min-w-0 flex-col gap-1">
                  <p className="text-base font-semibold text-slate-900">
                    {ALERT_TYPE_LABELS[p.alert_type]}: {p.device_id ?? 'Unknown device'}
                  </p>
                  <p className="text-sm text-slate-700">
                    {p.resident_id === 'unassigned' ? 'Not assigned to a resident' : `Resident ${p.resident_id}`}
                    {' · '}Raised <RelativeTime iso={p.event_ts} now={now} />
                  </p>
                  {detailText(p, now) && <p className="text-sm text-slate-700">{detailText(p, now)}</p>}
                  {p.status === 'acknowledged' && (
                    <p className="text-sm font-medium text-emerald-800">
                      Acknowledged by {p.acknowledged_by ?? 'a caregiver'}
                      {p.acknowledged_at && (
                        <>
                          {' '}
                          <RelativeTime iso={p.acknowledged_at} now={now} />
                        </>
                      )}
                    </p>
                  )}
                </div>
                {p.status === 'open' && (
                  <Button
                    variant="secondary"
                    busy={pending === p.alert_id}
                    disabled={pending !== null}
                    onClick={() => onAcknowledge(p)}
                    aria-label={`Acknowledge ${ALERT_TYPE_LABELS[p.alert_type]} on ${p.device_id ?? 'device'}`}
                  >
                    Acknowledge
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </Card>
  )
}

function OpsSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <LoadingStatus label="Loading device problems" />
      <StatTilesSkeleton />
      <Card className="flex flex-col gap-3 p-6">
        <Skeleton className="h-6 w-40" />
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </Card>
    </div>
  )
}
