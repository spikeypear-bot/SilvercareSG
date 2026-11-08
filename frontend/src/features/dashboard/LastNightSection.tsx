import { useId } from 'react'
import { Link } from 'react-router-dom'
import { TriangleAlert } from 'lucide-react'
import { Card } from '../../components/Card'
import { Notice } from '../../components/Notice'
import { Skeleton } from '../../components/Skeleton'
import type { Resident } from '../../types/dashboard'
import type { NightlyOverviewResponse } from '../../types/nightly'
import { DashboardError, LoadingStatus } from './DashboardStates'
import { flaggedReasonsText, lastNightCountLines, lastNightHeading } from './lastNight'
import type { LastNightState } from './useLastNight'

/**
 * The doctor dashboard's "Last night" section (docs/features/nightly-overview.md,
 * criteria 17-25): last night's flagged residents in the API's order, then the counts.
 * Doctor view only: the nurse dashboard doesn't render it. This only hides it; the API
 * refuses anyone not in `doctor`.
 */
export function LastNightSection({
  state,
  onRetry,
  residentsById,
}: {
  state: LastNightState
  onRetry: () => void
  residentsById: Map<string, Resident>
}) {
  const headingId = useId()
  const title = state.status === 'loaded' ? lastNightHeading(state.data.expected_date) : 'Last night'

  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-4">
        <h2 id={headingId} className="text-lg font-semibold text-slate-900">
          {title}
        </h2>
        {state.status === 'loading' ? (
          <div aria-busy="true" className="flex flex-col gap-3">
            <LoadingStatus label="Loading last night's overview" />
            <Skeleton className="h-6 w-3/4" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-6 w-1/2" />
          </div>
        ) : state.status === 'error' ? (
          <DashboardError title="We couldn't load last night's overview." onRetry={onRetry} />
        ) : (
          <LastNightContent data={state.data} residentsById={residentsById} />
        )}
      </section>
    </Card>
  )
}

function LastNightContent({
  data,
  residentsById,
}: {
  data: NightlyOverviewResponse
  residentsById: Map<string, Resident>
}) {
  if (!data.available) {
    return data.not_ready ? (
      <Notice tone="warning">Last night&apos;s summaries aren&apos;t ready yet.</Notice>
    ) : (
      <p className="text-base text-slate-600">Last night&apos;s summaries usually appear by 09:30.</p>
    )
  }

  const countLines = lastNightCountLines(data.clear_count, data.no_summary_count)
  return (
    <>
      {data.flagged.length === 0 ? (
        <p className="text-base text-slate-600">Nothing flagged last night.</p>
      ) : (
        <ol className="flex flex-col divide-y divide-slate-200">
          {data.flagged.map((entry) => (
            <li key={entry.resident_id} className="flex items-start gap-3 py-2 first:pt-0 last:pb-0">
              <TriangleAlert className="mt-3 size-5 shrink-0 text-amber-700" aria-hidden="true" />
              <p className="min-w-0 text-base text-slate-700">
                <Link
                  to={`/residents/${encodeURIComponent(entry.resident_id)}`}
                  className="inline-flex min-h-11 items-center rounded font-semibold text-slate-900 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40"
                >
                  {residentsById.get(entry.resident_id)?.name ?? entry.resident_id}
                </Link>{' '}
                <span className="font-medium text-amber-900">{flaggedReasonsText(entry)}</span>
              </p>
            </li>
          ))}
        </ol>
      )}
      {countLines.length > 0 && (
        <div className="flex flex-col gap-1">
          {countLines.map((line) => (
            <p key={line} className="text-base text-slate-600">
              {line}
            </p>
          ))}
        </div>
      )}
    </>
  )
}
