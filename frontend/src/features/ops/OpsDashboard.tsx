import { useCallback, useEffect, useId, useState, type ReactNode } from 'react'
import { BatteryLow, Cpu, Watch, WifiOff } from 'lucide-react'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { EmptyState } from '../../components/EmptyState'
import { Notice } from '../../components/Notice'
import { RelativeTime } from '../../components/RelativeTime'
import { Skeleton } from '../../components/Skeleton'
import { acknowledgeAlertApi, getDeviceProblems } from '../../lib/api'
import { useNow } from '../../lib/useNow'
import type { DeviceProblem, DeviceProblemType } from '../../types/ops'
import { AlertAnnouncer } from '../dashboard/AlertAnnouncer'
import { DashboardError, LoadingStatus } from '../dashboard/DashboardStates'
import { ALERT_TYPE_LABELS } from '../dashboard/labels'
import { MainLayout } from '../dashboard/MainLayout'
import { StatTile, StatTilesSkeleton } from '../dashboard/StatTile'
import { useAnnouncer } from '../dashboard/useAnnouncer'

/**
 * The ops view (docs/data-model.md, the ops paragraph): open and acknowledged device
 * problems for every resident, with Acknowledge. No health alerts, resident names, or
 * readings: the API sends none (GET /ops/device-problems filters server-side), and
 * every resident route answers ops with 403, so residents are shown by opaque ID only.
 * Device problems are resolved by the watchdog when they clear, so there is no resolve.
 */
export function OpsDashboard() {
  const [problems, setProblems] = useState<DeviceProblem[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let current = true
    getDeviceProblems()
      .then((result) => current && setProblems(result))
      .catch(() => current && setLoadError(true))
    return () => {
      current = false
    }
  }, [attempt])

  const retry = useCallback(() => {
    setLoadError(false)
    setProblems(null)
    setAttempt((n) => n + 1)
  }, [])

  return (
    <MainLayout title="Ops dashboard">
      {loadError ? (
        <DashboardError title="We couldn't load the device problems." onRetry={retry} />
      ) : problems === null ? (
        <OpsSkeleton />
      ) : (
        <OpsContent problems={problems} onChange={setProblems} />
      )}
    </MainLayout>
  )
}

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

function OpsContent({
  problems,
  onChange,
}: {
  problems: DeviceProblem[]
  onChange: (next: DeviceProblem[]) => void
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const [pending, setPending] = useState<string | null>(null)
  const [actionError, setActionError] = useState(false)
  const { message, announce } = useAnnouncer()

  const count = (f: Filter) => (f === 'all' ? problems.length : problems.filter((p) => p.alert_type === f).length)
  const visible = filter === 'all' ? problems : problems.filter((p) => p.alert_type === filter)
  const current = FILTERS.find((f) => f.id === filter) ?? FILTERS[0]

  function choose(next: Filter) {
    setFilter(next)
    const spec = FILTERS.find((f) => f.id === next) ?? FILTERS[0]
    announce(`Showing ${count(next)} of ${problems.length}: ${spec.spoken}.`)
  }

  async function acknowledge(problem: DeviceProblem) {
    setPending(problem.alert_id)
    setActionError(false)
    try {
      await acknowledgeAlertApi(problem, null)
      // Queued (202); alert-processor applies it. Shown at once, as the other views do.
      onChange(
        problems.map((p) =>
          p.alert_id === problem.alert_id
            ? {
                ...p,
                status: 'acknowledged',
                acknowledged_at: new Date().toISOString(),
                acknowledged_by_role: 'ops',
                acknowledged_by: 'the ops team',
              }
            : p,
        ),
      )
      announce(`${ALERT_TYPE_LABELS[problem.alert_type]} on ${problem.device_id ?? 'the device'} acknowledged.`)
    } catch {
      setActionError(true)
    } finally {
      setPending(null)
    }
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
