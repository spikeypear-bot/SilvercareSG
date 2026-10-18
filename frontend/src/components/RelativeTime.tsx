import { formatExactTime, formatRelativeTime } from '../lib/format'

/** "12 min ago", with the exact Singapore time in <time> and a tooltip. */
export function RelativeTime({ iso, now }: { iso: string; now?: Date }) {
  const exact = formatExactTime(iso)
  return (
    <time dateTime={iso} title={exact}>
      {formatRelativeTime(iso, now)}
      <span className="sr-only"> ({exact})</span>
    </time>
  )
}
