import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { CircleCheck, TriangleAlert } from 'lucide-react'
import { Button } from '../../components/Button'
import { Notice } from '../../components/Notice'
import { Skeleton } from '../../components/Skeleton'
import { getNightly } from '../../lib/api'
import type { Night, NightlyResponse } from '../../types/nightly'
import { DashboardError, LoadingStatus } from '../dashboard/DashboardStates'
import { DetailSection } from './DetailSection'
import {
  anomalyLine,
  formatActivityAvg,
  formatBedTime,
  formatCount,
  formatLimitsUsed,
  formatNightDate,
  formatReadings,
  sortNightsNewestFirst,
  vitalFigures,
} from './nightly'

const TITLE = 'Nightly summary'

type NightlyState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'loaded'; data: NightlyResponse; nights: Night[] }

/** Loads once per resident (no polling); `retry` reloads only this section. */
function useNightly(residentId: string) {
  const [state, setState] = useState<NightlyState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    getNightly(residentId)
      .then((data) => {
        if (!cancelled) setState({ status: 'loaded', data, nights: sortNightsNewestFirst(data.nights) })
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'error' })
      })
    return () => {
      cancelled = true
    }
  }, [residentId, attempt])

  const retry = useCallback(() => {
    setState({ status: 'loading' })
    setAttempt((a) => a + 1)
  }, [])

  return { state, retry }
}

/**
 * The resident's stored nights (up to 30) from `GET /residents/{id}/nightly`, one at a
 * time, compared with that night's own thresholds (docs/features/nightly-results.md,
 * criteria 24-30). Doctor view only: the parent doesn't render it for nurses. This only
 * hides it; the API refuses anyone not in `doctor`.
 */
export function NightlySummary({ residentId }: { residentId: string }) {
  const { state, retry } = useNightly(residentId)

  if (state.status === 'loading') {
    return (
      <DetailSection title={TITLE}>
        <div aria-busy="true" className="flex flex-col gap-3">
          <LoadingStatus label="Loading nightly summary" />
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-6 w-3/4" />
        </div>
      </DetailSection>
    )
  }

  if (state.status === 'error') {
    return (
      <DetailSection title={TITLE}>
        <DashboardError title="We couldn't load the nightly summaries." onRetry={retry} />
      </DetailSection>
    )
  }

  const { data, nights } = state
  if (nights.length === 0) {
    return (
      <DetailSection title={TITLE}>
        {data.not_ready ? (
          <Notice tone="warning">Last night&apos;s summary isn&apos;t ready yet.</Notice>
        ) : (
          <p className="text-base text-slate-600">
            No nightly summaries yet. They appear each morning after a night of readings.
          </p>
        )}
      </DetailSection>
    )
  }

  return <NightPicker nights={nights} notReady={data.not_ready} />
}

/** The picker, the not-ready banner, and the chosen night's figures. `nights` is newest first and not empty. */
function NightPicker({ nights, notReady }: { nights: Night[]; notReady: boolean }) {
  const selectId = useId()
  const selectRef = useRef<HTMLSelectElement>(null)
  const [selectedDate, setSelectedDate] = useState(nights[0].date)
  // When last night's summary is missing, the latest night stays hidden until the doctor
  // asks for it (the button) or picks a night.
  const [revealed, setRevealed] = useState(!notReady)
  const latest = nights[0]
  const latestLabel = formatNightDate(latest.date)
  const selected = nights.find((n) => n.date === selectedDate) ?? latest

  const picker = (
    <div className="flex items-center gap-2">
      <label htmlFor={selectId} className="text-base font-medium text-slate-800">
        Night
      </label>
      <select
        id={selectId}
        ref={selectRef}
        value={selected.date}
        onChange={(e) => {
          setSelectedDate(e.target.value)
          setRevealed(true)
        }}
        className="min-h-11 rounded-xl border border-slate-300 bg-white px-3 py-2 text-base text-slate-900 focus-visible:border-teal-600 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/30"
      >
        {nights.map((night) => (
          <option key={night.date} value={night.date}>
            {formatNightDate(night.date)}
          </option>
        ))}
      </select>
    </div>
  )

  return (
    <DetailSection
      title={TITLE}
      description="One night at a time, compared with the limits used that night."
      action={picker}
    >
      {notReady && (
        <Notice
          tone="warning"
          action={
            revealed ? undefined : (
              <Button
                variant="secondary"
                onClick={() => {
                  setSelectedDate(latest.date)
                  setRevealed(true)
                  // The button goes away; keep focus on the section's control.
                  selectRef.current?.focus()
                }}
              >
                {`View ${latestLabel}`}
              </Button>
            )
          }
        >
          {`Last night's summary isn't ready yet (latest: ${latestLabel})`}
        </Notice>
      )}
      {revealed && <NightFigures night={selected} />}
    </DetailSection>
  )
}

function NightFigures({ night }: { night: Night }) {
  const vitals = vitalFigures(night)
  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-base font-semibold text-slate-900">{formatNightDate(night.date)}</h3>
      <dl className="flex flex-col divide-y divide-slate-200">
        {vitals.map((vital) => (
          <div
            key={vital.label}
            className="flex flex-col gap-1 py-3 first:pt-0 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-6"
          >
            <dt className="text-base font-medium text-slate-900 sm:w-32">{vital.label}</dt>
            <dd className="text-base text-slate-700 sm:min-w-56">{vital.stats ?? 'No readings'}</dd>
            {vital.stats !== null && (
              <dd>
                <VitalFlags flags={vital.flags} />
              </dd>
            )}
          </div>
        ))}
      </dl>
      <ul className="grid gap-x-6 gap-y-2 text-base text-slate-700 sm:grid-cols-2">
        <li>
          {night.activity_pct_avg === null ? 'Activity: No readings' : `Activity avg ${formatActivityAvg(night.activity_pct_avg)}`}
        </li>
        <li>{formatReadings(night.vitals_readings)}</li>
        <li>{night.bed_minutes === null ? 'No bed data' : `Time in bed ${formatBedTime(night.bed_minutes)}`}</li>
        <li>{`Motion events ${formatCount(night.motion_events)}`}</li>
        <li>{`Falls ${formatCount(night.falls)}`}</li>
        <li>{`Alert episodes ${formatCount(night.vitals_alert_episodes)}`}</li>
        <li>{`"I'm OK" cancels ${formatCount(night.cancels)}`}</li>
      </ul>
      <p className="text-base text-slate-600">{`Limits used that night: ${formatLimitsUsed(night.thresholds)}`}</p>
      <AnomalyCheck night={night} />
    </div>
  )
}

/** The anomaly model's view of the night (docs/features/nightly-anomaly.md, criterion 19). */
function AnomalyCheck({ night }: { night: Night }) {
  const line = anomalyLine(night.anomaly)
  return (
    <div className="flex flex-col gap-1 border-t border-slate-200 pt-3 sm:flex-row sm:gap-x-6">
      <p className="text-base font-medium text-slate-900 sm:w-32">Anomaly check</p>
      <div className="flex flex-col gap-1">
        {line.unusual ? (
          <p className="inline-flex items-center gap-1 text-base font-semibold text-amber-900">
            <TriangleAlert className="size-5 shrink-0" aria-hidden="true" />
            <span>{line.text}</span>
          </p>
        ) : (
          <p className="text-base text-slate-700">{line.text}</p>
        )}
        {line.detail && <p className="text-base text-slate-700">{line.detail}</p>}
      </div>
    </div>
  )
}

/** "12 above 120" / "3 below 50", each with an icon and text (never colour alone), or "In range". */
function VitalFlags({ flags }: { flags: string[] }) {
  if (flags.length === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-base font-medium text-emerald-800">
        <CircleCheck className="size-5 shrink-0" aria-hidden="true" />
        <span>In range</span>
      </span>
    )
  }
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1">
      {flags.map((flag) => (
        <li key={flag} className="inline-flex items-center gap-1 text-base font-semibold text-amber-900">
          <TriangleAlert className="size-5 shrink-0" aria-hidden="true" />
          <span>{flag}</span>
        </li>
      ))}
    </ul>
  )
}
