import { useId, useMemo, useState, type ReactNode } from 'react'
import { Bell, BatteryLow, Search, Users, WifiOff } from 'lucide-react'
import { Avatar } from '../../components/Avatar'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { EmptyState } from '../../components/EmptyState'
import { RelativeTime } from '../../components/RelativeTime'
import { Skeleton } from '../../components/Skeleton'
import { StatusBadge } from '../../components/StatusBadge'
import { TextField } from '../../components/TextField'
import { cx } from '../../lib/cx'
import { useNow } from '../../lib/useNow'
import type { DashboardData, Resident, ResidentAlert, ResidentStatus } from '../../types/dashboard'
import { BatteryLevel, WearableStatusText } from './DeviceStatus'
import { DashboardError, LoadingStatus } from './DashboardStates'
import {
  ALERT_TYPE_LABELS,
  LOW_BATTERY_DISPLAY_PCT,
  formatHr,
  formatSpo2,
  formatTemp,
} from './labels'
import { MainLayout } from './MainLayout'
import { useDashboardData } from './useDashboardData'

export function StaffDashboard() {
  const { data, loading, error, retry } = useDashboardData('staff')

  return (
    <MainLayout title="Staff dashboard">
      {error ? (
        <DashboardError onRetry={retry} />
      ) : loading || !data ? (
        <StaffSkeleton />
      ) : (
        <StaffContent data={data} />
      )}
    </MainLayout>
  )
}

// ---------------------------------------------------------------------------

function StaffContent({ data }: { data: DashboardData }) {
  const residentsById = useMemo(
    () => new Map(data.residents.map((r) => [r.resident_id, r])),
    [data.residents],
  )
  return (
    <>
      <StatTiles data={data} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <AlertsPanel alerts={data.alerts} residentsById={residentsById} />
        <ResidentsPanel residents={data.residents} />
      </div>
    </>
  )
}

function StatTile({
  label,
  value,
  detail,
  icon,
}: {
  label: string
  value: number
  detail?: string
  icon: ReactNode
}) {
  return (
    <Card className="flex items-start gap-4 p-5">
      <span className="inline-flex size-11 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-800">
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-base text-slate-600">{label}</p>
        <p className="text-3xl font-bold tracking-tight text-slate-900">{value}</p>
        {detail && <p className="text-sm font-medium text-slate-700">{detail}</p>}
      </div>
    </Card>
  )
}

function StatTiles({ data }: { data: DashboardData }) {
  const critical = data.alerts.filter((a) => a.severity === 'critical').length
  const devicesOffline = data.residents.reduce(
    (n, r) => n + (r.wearable.online ? 0 : 1) + (r.bedroom.online ? 0 : 1),
    0,
  )
  const lowBattery = data.residents.filter(
    (r) => r.wearable.battery_pct < LOW_BATTERY_DISPLAY_PCT,
  ).length

  return (
    <section aria-labelledby="stats-heading">
      <h2 id="stats-heading" className="sr-only">
        Summary
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Residents monitored"
          value={data.residents.length}
          icon={<Users className="size-6" aria-hidden="true" />}
        />
        <StatTile
          label="Open alerts"
          value={data.alerts.length}
          detail={`${critical} critical`}
          icon={<Bell className="size-6" aria-hidden="true" />}
        />
        <StatTile
          label="Devices offline"
          value={devicesOffline}
          icon={<WifiOff className="size-6" aria-hidden="true" />}
        />
        <StatTile
          label="Wearables low on battery"
          value={lowBattery}
          detail={`Below ${LOW_BATTERY_DISPLAY_PCT}%`}
          icon={<BatteryLow className="size-6" aria-hidden="true" />}
        />
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------

function AlertsPanel({
  alerts,
  residentsById,
}: {
  alerts: ResidentAlert[]
  residentsById: Map<string, Resident>
}) {
  const headingId = useId()
  // Newest first, regardless of the order the data arrives in.
  const sorted = useMemo(
    () => [...alerts].sort((a, b) => b.event_ts.localeCompare(a.event_ts)),
    [alerts],
  )
  const now = useNow()

  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-4">
        <h2 id={headingId} className="text-lg font-semibold text-slate-900">
          Open alerts <span className="font-normal text-slate-600">({alerts.length})</span>
        </h2>
        <div aria-live="polite">
          {sorted.length === 0 ? (
            <p className="text-base text-slate-600">No open alerts.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-slate-200">
              {sorted.map((alert) => {
                const resident = residentsById.get(alert.resident_id)
                return (
                  <li key={alert.alert_id} className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <StatusBadge tone={alert.severity} />
                      <span className="text-sm text-slate-600">
                        <RelativeTime iso={alert.event_ts} now={now} />
                      </span>
                    </div>
                    <p className="text-base font-semibold text-slate-900">
                      {ALERT_TYPE_LABELS[alert.alert_type]}
                    </p>
                    <p className="text-base text-slate-700">
                      {resident?.name ?? 'Unknown resident'}{' '}
                      <span className="text-slate-600">· {alert.resident_id}</span>
                    </p>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </section>
    </Card>
  )
}

// ---------------------------------------------------------------------------

type Filter = 'all' | ResidentStatus

const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'urgent', label: 'Urgent' },
  { id: 'attention', label: 'Attention' },
  { id: 'ok', label: 'OK' },
]

function matchesSearch(resident: Resident, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return resident.name.toLowerCase().includes(q) || resident.resident_id.toLowerCase().includes(q)
}

function ResidentsPanel({ residents }: { residents: Resident[] }) {
  const headingId = useId()
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: residents.length, urgent: 0, attention: 0, ok: 0 }
    residents.forEach((r) => {
      c[r.status] += 1
    })
    return c
  }, [residents])

  const visible = residents.filter(
    (r) => (filter === 'all' || r.status === filter) && matchesSearch(r, query),
  )
  const now = useNow()

  function clearFilters() {
    setQuery('')
    setFilter('all')
  }

  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-5">
        <h2 id={headingId} className="text-lg font-semibold text-slate-900">
          Residents
        </h2>

        <div className="flex flex-col gap-4">
          <TextField
            label="Search residents"
            type="search"
            placeholder="Name or resident ID"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            endAdornment={<Search className="mr-3 size-5 text-slate-500" aria-hidden="true" />}
          />
          <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-2">
            {FILTERS.map(({ id, label }) => {
              const active = filter === id
              return (
                <button
                  key={id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setFilter(id)}
                  className={cx(
                    'inline-flex min-h-11 items-center gap-2 rounded-full border px-4 text-base font-medium transition-colors focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40',
                    active
                      ? 'border-teal-700 bg-teal-700 text-white'
                      : 'border-slate-300 bg-white text-slate-800 hover:bg-slate-50',
                  )}
                >
                  {label}
                  <span
                    className={cx(
                      'rounded-full px-2 text-sm',
                      active ? 'bg-teal-900 text-white' : 'bg-slate-100 text-slate-700',
                    )}
                  >
                    {counts[id]}
                  </span>
                </button>
              )
            })}
          </div>
          <p className="text-sm text-slate-600" aria-live="polite">
            Showing {visible.length} of {residents.length} residents
          </p>
        </div>

        {visible.length === 0 ? (
          <EmptyState
            title="No residents match your search"
            description="Try a different name or resident ID, or clear the filters."
            action={
              <Button variant="secondary" onClick={clearFilters}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
            {visible.map((resident) => (
              <li key={resident.resident_id}>
                <ResidentCard resident={resident} now={now} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </Card>
  )
}

function ResidentCard({ resident, now }: { resident: Resident; now: Date }) {
  const latest = resident.vitals[0]
  const lastUpdated =
    latest && latest.ts > resident.wearable.ts ? latest.ts : resident.wearable.ts

  return (
    <article className="flex h-full flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex items-start gap-3">
        <Avatar name={resident.name} />
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold text-slate-900">{resident.name}</h3>
          <p className="text-sm text-slate-600">{resident.resident_id}</p>
        </div>
        <StatusBadge tone={resident.status} />
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-base">
        <Metric label="Heart rate" value={formatHr(latest?.hr_bpm ?? null)} />
        <Metric label="SpO2" value={formatSpo2(latest?.spo2_pct ?? null)} />
        <Metric label="Skin temp" value={formatTemp(latest?.skin_temp_c ?? null)} />
        <Metric label="Battery" value={<BatteryLevel wearable={resident.wearable} />} />
      </dl>
      <div className="mt-auto flex flex-col gap-1 border-t border-slate-100 pt-3 text-sm text-slate-700">
        <WearableStatusText wearable={resident.wearable} />
        <p className="text-slate-600">
          Last updated <RelativeTime iso={lastUpdated} now={now} />
        </p>
      </div>
    </article>
  )
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-slate-600">{label}</dt>
      <dd className="font-semibold text-slate-900">{value}</dd>
    </div>
  )
}

// ---------------------------------------------------------------------------

function StaffSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <LoadingStatus label="Loading dashboard" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Card key={i} className="flex items-start gap-4 p-5">
            <Skeleton className="size-11 rounded-xl" />
            <div className="flex flex-1 flex-col gap-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-8 w-12" />
            </div>
          </Card>
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-36" />
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </Card>
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-28" />
          <Skeleton className="h-11 w-full" />
          <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-44 w-full rounded-2xl" />
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
