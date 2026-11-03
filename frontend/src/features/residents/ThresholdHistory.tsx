import { RelativeTime } from '../../components/RelativeTime'
import type { ThresholdChangeEntry } from '../../types/resident'
import { DetailSection } from './DetailSection'
import { formatThresholdValue, thresholdName } from './thresholds'

interface ThresholdHistoryProps {
  /** Newest first. */
  history: ThresholdChangeEntry[]
  now: Date
}

/**
 * Earlier threshold changes (pattern 25). Read-only for every role: entries are written by
 * the server when a doctor saves (with the pattern 24 route), never by the UI.
 */
export function ThresholdHistory({ history, now }: ThresholdHistoryProps) {
  const sorted = [...history].sort((a, b) => b.changed_at.localeCompare(a.changed_at))

  return (
    <DetailSection title="Change history" description="Earlier changes to the vitals thresholds, newest first.">
      {sorted.length === 0 ? (
        <p className="text-base text-slate-600">No changes to these thresholds have been recorded.</p>
      ) : (
        <ol className="flex flex-col divide-y divide-slate-200">
          {sorted.map((entry) => (
            <li key={entry.change_id} className="flex flex-col gap-2 py-4 first:pt-0 last:pb-0">
              <p className="text-base text-slate-700">
                Changed by <span className="font-semibold text-slate-900">{entry.changed_by}</span>,{' '}
                <RelativeTime iso={entry.changed_at} now={now} />
              </p>
              <ul className="flex flex-col gap-1 text-base text-slate-900">
                {entry.changes.map((change) => (
                  <li key={change.field}>
                    <span className="font-medium">{thresholdName(change.field)}:</span> from{' '}
                    {formatThresholdValue(change.field, change.old_value)} to{' '}
                    {formatThresholdValue(change.field, change.new_value)}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </DetailSection>
  )
}
