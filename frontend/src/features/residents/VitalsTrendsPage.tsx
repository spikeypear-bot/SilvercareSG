import { useCallback, useEffect, useId, useRef, useState, type Ref } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Download } from 'lucide-react'
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Button, buttonBase } from '../../components/Button'
import { Card } from '../../components/Card'
import { Notice } from '../../components/Notice'
import { Skeleton } from '../../components/Skeleton'
import { ApiError, getResident, getVitals } from '../../lib/api'
import { cx } from '../../lib/cx'
import type { NightThresholds } from '../../types/nightly'
import type { VitalsInterval, VitalsResponse, VitalsRow } from '../../types/vitals'
import { DashboardError, LoadingStatus } from '../dashboard/DashboardStates'
import { MainLayout } from '../dashboard/MainLayout'
import { StaffOnly } from '../dashboard/StaffOnly'
import { DetailSection } from './DetailSection'
import {
  DEFAULT_DAYS,
  PRESETS,
  VALUE_COLUMNS,
  csvFilename,
  dayCount,
  earliestDate,
  latestDate,
  outOfLimit,
  presetRange,
  rangeProblem,
  rowLabel,
  toCsv,
} from './vitalsTrends'

/** Header and `document.title`: never the resident's name. */
const TITLE = 'Vitals trends'

type Metric = 'hr_bpm' | 'spo2_pct' | 'skin_temp_c'
const METRICS: Array<{ key: Metric; label: string; unit: string }> = [
  { key: 'hr_bpm', label: 'Heart rate', unit: 'bpm' },
  { key: 'spo2_pct', label: 'SpO2', unit: '%' },
  { key: 'skin_temp_c', label: 'Skin temperature', unit: '°C' },
]

/**
 * `/residents/:id/vitals` (docs/features/vitals-trends.md): doctors only. A nurse view
 * (including a doctor + nurse user) gets a "for doctors" notice; caregivers and others
 * get `StaffOnly`'s screens. Nothing loads for them. The API refuses non-doctors anyway.
 */
export function VitalsTrendsPage() {
  return <StaffOnly>{(role) => (role === 'doctor' ? <DoctorVitals /> : <DoctorsOnlyNotice />)}</StaffOnly>
}

function DoctorsOnlyNotice() {
  const { id = '' } = useParams()
  return (
    <MainLayout title={TITLE} showGreeting={false} before={<BackLink residentId={id} />}>
      <Card className="flex flex-col items-start gap-2 p-6">
        <h2 className="text-xl font-semibold text-slate-900">This page is for doctors.</h2>
        <p className="text-base text-slate-700">The resident page shows the latest readings and alerts.</p>
      </Card>
    </MainLayout>
  )
}

function BackLink({ residentId, ref }: { residentId: string; ref?: Ref<HTMLAnchorElement> }) {
  return (
    <Link
      ref={ref}
      to={`/residents/${encodeURIComponent(residentId)}`}
      className={cx(buttonBase, 'self-start px-2 text-teal-800 hover:bg-teal-50')}
    >
      <ArrowLeft className="size-5" aria-hidden="true" />
      Back to resident
    </Link>
  )
}

type Load =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; timeout: boolean }
  | { status: 'loaded'; data: VitalsResponse }

/** Loads when the request changes; a result for an older request reads as loading. */
function useVitals(residentId: string, interval: VitalsInterval, from: string, to: string, valid: boolean) {
  const [result, setResult] = useState<{ key: string; load: Load } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const key = `${residentId}|${interval}|${from}|${to}|${attempt}`
  useEffect(() => {
    if (!valid) return
    let cancelled = false
    getVitals(residentId, interval, from, to)
      .then((data) => {
        if (!cancelled) setResult({ key, load: { status: 'loaded', data } })
      })
      .catch((err: unknown) => {
        const timeout = err instanceof ApiError && err.status === 504
        if (!cancelled) setResult({ key, load: { status: 'error', timeout } })
      })
    return () => {
      cancelled = true
    }
  }, [key, residentId, interval, from, to, valid])
  const retry = useCallback(() => setAttempt((a) => a + 1), [])
  const state: Load = !valid ? { status: 'idle' } : result?.key === key ? result.load : { status: 'loading' }
  return { state, retry }
}

/** The resident's name for the heading, from GET /residents/{id} (pattern 18). */
function useResidentName(residentId: string): string | null {
  const [name, setName] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    getResident(residentId)
      .then((detail) => {
        if (!cancelled) setName(detail.resident.display_name)
      })
      .catch(() => {
        // The heading falls back to "Resident"; the trends still load.
      })
    return () => {
      cancelled = true
    }
  }, [residentId])
  return name
}

function DoctorVitals() {
  const { id = '' } = useParams()
  const backRef = useRef<HTMLAnchorElement>(null)
  // Fixed for the page's life: "yesterday" doesn't move while a doctor is reading.
  const [now] = useState(() => Date.now())
  const [interval, setIntervalChoice] = useState<VitalsInterval>('day')
  const [range, setRange] = useState(() => presetRange(DEFAULT_DAYS.day, now))
  const [metric, setMetric] = useState<Metric>('hr_bpm')
  const problem = rangeProblem(interval, range.from, range.to, now)
  const { state, retry } = useVitals(id, interval, range.from, range.to, problem === null)
  const name = useResidentName(id)
  const fromId = useId()
  const toId = useId()

  useEffect(() => {
    backRef.current?.focus()
  }, [id])

  function chooseInterval(next: VitalsInterval) {
    setIntervalChoice(next)
    setRange(presetRange(DEFAULT_DAYS[next], now))
  }

  const latest = latestDate(now)
  const presetDays = range.to === latest ? dayCount(range.from, range.to) : null

  return (
    <MainLayout title={TITLE} showGreeting={false} before={<BackLink residentId={id} ref={backRef} />}>
      <h2 className="text-2xl font-semibold text-slate-900">{name ?? 'Resident'}</h2>
      <Card className="flex flex-col gap-4 p-5 sm:p-6">
        <fieldset className="flex flex-wrap items-center gap-2">
          <legend className="mb-2 text-base font-medium text-slate-800">Interval</legend>
          {(['day', 'hour'] as const).map((value) => (
            <Button
              key={value}
              variant={interval === value ? 'primary' : 'secondary'}
              aria-pressed={interval === value}
              onClick={() => chooseInterval(value)}
            >
              {value === 'day' ? 'Daily' : 'Hourly'}
            </Button>
          ))}
        </fieldset>
        <fieldset className="flex flex-wrap items-end gap-3">
          <legend className="mb-2 text-base font-medium text-slate-800">Dates</legend>
          {PRESETS[interval].map((days) => (
            <Button
              key={days}
              variant={presetDays === days ? 'primary' : 'secondary'}
              aria-pressed={presetDays === days}
              onClick={() => setRange(presetRange(days, now))}
            >
              {days === 1 ? 'Yesterday' : `Last ${days} days`}
            </Button>
          ))}
          <div className="flex flex-col gap-1">
            <label htmlFor={fromId} className="text-base text-slate-800">
              From
            </label>
            <input
              id={fromId}
              type="date"
              value={range.from}
              min={earliestDate(interval, now)}
              max={latest}
              onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
              className="min-h-11 rounded-md border border-slate-300 px-3 text-base"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={toId} className="text-base text-slate-800">
              To
            </label>
            <input
              id={toId}
              type="date"
              value={range.to}
              min={earliestDate(interval, now)}
              max={latest}
              onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
              className="min-h-11 rounded-md border border-slate-300 px-3 text-base"
            />
          </div>
        </fieldset>
        <p className="text-base text-slate-600">
          Data up to yesterday. Yesterday&apos;s data is ready from about 09:00. Today&apos;s readings are on
          the resident page.
        </p>
        {problem && <Notice tone="warning">{problem}</Notice>}
      </Card>

      {problem === null && (
        <Results state={state} retry={retry} metric={metric} setMetric={setMetric} residentId={id} />
      )}
    </MainLayout>
  )
}

function Results({
  state,
  retry,
  metric,
  setMetric,
  residentId,
}: {
  state: Load
  retry: () => void
  metric: Metric
  setMetric: (m: Metric) => void
  residentId: string
}) {
  if (state.status === 'idle' || state.status === 'loading') {
    return (
      <DetailSection title="Results">
        <div aria-busy="true" className="flex flex-col gap-3">
          <LoadingStatus label="Loading vitals trends" />
          <p className="text-base text-slate-600">This can take a few seconds.</p>
          <Skeleton className="h-64 w-full" />
        </div>
      </DetailSection>
    )
  }
  if (state.status === 'error') {
    return (
      <DetailSection title="Results">
        {state.timeout ? (
          <DashboardError title="This took too long. Try a shorter range." onRetry={retry} />
        ) : (
          <DashboardError title="We couldn't load the vitals trends." onRetry={retry} />
        )}
      </DetailSection>
    )
  }
  const { data } = state
  if (data.rows.every((r) => !r.has_data)) {
    return (
      <DetailSection title="Results">
        <p className="text-base text-slate-600">No readings for this resident in this range.</p>
      </DetailSection>
    )
  }
  return (
    <>
      <VitalsChart data={data} metric={metric} setMetric={setMetric} />
      <CountsChart rows={data.rows} />
      {/* A new range starts the table at page 1. */}
      <ResultsTable key={`${data.interval}-${data.from}-${data.to}`} data={data} residentId={residentId} />
    </>
  )
}

/** The limits to draw: hourly the current ones, daily the newest stored day's. */
function limitsFor(data: VitalsResponse): Partial<NightThresholds> | null {
  if (data.thresholds) return data.thresholds
  const stored = [...data.rows].reverse().find((r) => r.has_data && r.thresholds)
  return stored?.thresholds ?? null
}

function chartRows(rows: VitalsRow[], metric: Metric) {
  return rows.map((row) => {
    const min = row[`${metric}_min`]
    const max = row[`${metric}_max`]
    return {
      label: rowLabel(row),
      range: min !== null && max !== null ? [min, max] : null,
      avg: row[`${metric}_avg`],
    }
  })
}

function VitalsChart({
  data,
  metric,
  setMetric,
}: {
  data: VitalsResponse
  metric: Metric
  setMetric: (m: Metric) => void
}) {
  const info = METRICS.find((m) => m.key === metric) ?? METRICS[0]
  const points = chartRows(data.rows, metric)
  const limits = limitsFor(data)?.[metric] as { min?: number; max?: number } | undefined
  const avgs = points.map((p) => p.avg).filter((v): v is number => v !== null)
  const summary = avgs.length
    ? `${info.label} average from ${Math.min(...avgs)} to ${Math.max(...avgs)} ${info.unit} across ${avgs.length} of ${points.length} ${data.interval === 'hour' ? 'hours' : 'days'} with readings. The table below has every value.`
    : `No ${info.label.toLowerCase()} readings in this range.`
  return (
    <DetailSection title="Vitals">
      <div role="group" aria-label="Metric" className="flex flex-wrap gap-2">
        {METRICS.map((m) => (
          <Button
            key={m.key}
            variant={metric === m.key ? 'primary' : 'secondary'}
            aria-pressed={metric === m.key}
            onClick={() => setMetric(m.key)}
          >
            {m.label}
          </Button>
        ))}
      </div>
      <p className="text-base text-slate-700">{summary}</p>
      <figure aria-label={`${info.label} chart: ${summary}`} className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid stroke="#e2e8f0" />
            <XAxis dataKey="label" tick={{ fontSize: 14 }} minTickGap={24} />
            <YAxis tick={{ fontSize: 14 }} domain={['auto', 'auto']} unit={` ${info.unit}`} width={80} />
            <Tooltip />
            <Legend />
            <Area dataKey="range" name={`${info.label} min–max`} fill="#99f6e4" stroke="#0f766e" connectNulls={false} />
            <Line dataKey="avg" name={`${info.label} average`} stroke="#134e4a" dot={false} connectNulls={false} />
            {limits?.min !== undefined && (
              <ReferenceLine y={limits.min} stroke="#b45309" strokeDasharray="6 4" label={`Min limit ${limits.min}`} />
            )}
            {limits?.max !== undefined && (
              <ReferenceLine y={limits.max} stroke="#b45309" strokeDasharray="6 4" label={`Max limit ${limits.max}`} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </figure>
    </DetailSection>
  )
}

function CountsChart({ rows }: { rows: VitalsRow[] }) {
  const points = rows.map((row) => ({
    label: rowLabel(row),
    falls: row.falls ?? 0,
    episodes: row.vitals_alert_episodes ?? 0,
    cancels: row.cancels ?? 0,
    outside: outOfLimit(row),
  }))
  const totals = points.reduce(
    (t, p) => ({
      falls: t.falls + p.falls,
      episodes: t.episodes + p.episodes,
      cancels: t.cancels + p.cancels,
      outside: t.outside + p.outside,
    }),
    { falls: 0, episodes: 0, cancels: 0, outside: 0 },
  )
  const summary = `In this range: ${totals.falls} falls, ${totals.episodes} vitals alert episodes, ${totals.cancels} "I'm OK" cancels, ${totals.outside} readings outside the limits.`
  return (
    <DetailSection title="Events">
      <p className="text-base text-slate-700">{summary}</p>
      <figure aria-label={`Events chart: ${summary}`} className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={points} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid stroke="#e2e8f0" />
            <XAxis dataKey="label" tick={{ fontSize: 14 }} minTickGap={24} />
            <YAxis allowDecimals={false} tick={{ fontSize: 14 }} width={48} />
            <Tooltip />
            <Legend />
            <Bar dataKey="falls" name="Falls" stackId="e" fill="#b91c1c" />
            <Bar dataKey="episodes" name="Vitals alert episodes" stackId="e" fill="#c2410c" />
            <Bar dataKey="cancels" name={'"I\'m OK" cancels'} stackId="e" fill="#0369a1" />
            <Bar dataKey="outside" name="Readings outside limits" stackId="e" fill="#a16207" />
          </BarChart>
        </ResponsiveContainer>
      </figure>
    </DetailSection>
  )
}

/** Table rows per page: one day of hourly rows. The charts and the CSV keep every row. */
const TABLE_PAGE_SIZE = 24

function ResultsTable({ data, residentId }: { data: VitalsResponse; residentId: string }) {
  const [page, setPage] = useState(0)
  const pages = Math.ceil(data.rows.length / TABLE_PAGE_SIZE)
  const first = page * TABLE_PAGE_SIZE
  const shown = data.rows.slice(first, first + TABLE_PAGE_SIZE)
  function download() {
    const blob = new Blob([toCsv(residentId, data.interval, data.rows)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = csvFilename(residentId, data.interval, data.from, data.to)
    link.click()
    URL.revokeObjectURL(url)
  }
  return (
    <DetailSection
      title="Table"
      action={
        <Button variant="secondary" onClick={download}>
          <Download className="size-5" aria-hidden="true" />
          Download CSV
        </Button>
      }
    >
      <p className="text-base text-slate-600">
        The download contains health data. Store it securely and delete it when you&apos;re done.
      </p>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-base">
          <caption className="sr-only">
            Vitals per {data.interval === 'hour' ? 'hour' : 'day'}, oldest first
          </caption>
          <thead>
            <tr className="border-b border-slate-200">
              <th scope="col" className="whitespace-nowrap px-3 py-2 font-medium">
                {data.interval === 'hour' ? 'Hour' : 'Date'}
              </th>
              {VALUE_COLUMNS.map((c) => (
                <th key={c.key} scope="col" className="whitespace-nowrap px-3 py-2 font-medium">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <tr key={row.hour ?? row.date} className="border-b border-slate-100">
                <th scope="row" className="whitespace-nowrap px-3 py-2 font-normal">
                  {rowLabel(row)}
                  {!row.has_data && <span className="ml-2 text-slate-600">No data</span>}
                </th>
                {VALUE_COLUMNS.map((c) => (
                  <td key={c.key} className="px-3 py-2 tabular-nums">
                    {row[c.key] ?? '–'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <nav aria-label="Table pages" className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" onClick={() => setPage(page - 1)} disabled={page === 0}>
            Previous
          </Button>
          <p aria-live="polite" className="text-base text-slate-700">
            {`Rows ${first + 1}–${first + shown.length} of ${data.rows.length}`}
          </p>
          <Button variant="secondary" onClick={() => setPage(page + 1)} disabled={page === pages - 1}>
            Next
          </Button>
        </nav>
      )}
    </DetailSection>
  )
}
