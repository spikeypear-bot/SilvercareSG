import { dayPart, formatLongDate } from '../../lib/format'
import { useNow } from '../../lib/useNow'

/** "Good morning, <name>" in Singapore time, plus today's date. */
export function Greeting({ name }: { name: string }) {
  const now = useNow()
  const greeting = `Good ${dayPart(now)}${name ? `, ${name}` : ''}`
  return (
    <div>
      <p className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">{greeting}</p>
      <p className="mt-1 text-base text-slate-600">
        <time dateTime={now.toISOString()}>{formatLongDate(now)}</time>
      </p>
    </div>
  )
}
