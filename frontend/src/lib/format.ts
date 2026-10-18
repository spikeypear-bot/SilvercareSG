/** Date and time formatting. All times are shown in Singapore time. */
export const SG_TIME_ZONE = 'Asia/Singapore'

const exactFormatter = new Intl.DateTimeFormat('en-SG', {
  timeZone: SG_TIME_ZONE,
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

const timeFormatter = new Intl.DateTimeFormat('en-SG', {
  timeZone: SG_TIME_ZONE,
  hour: 'numeric',
  minute: '2-digit',
})

const longDateFormatter = new Intl.DateTimeFormat('en-SG', {
  timeZone: SG_TIME_ZONE,
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

const hourFormatter = new Intl.DateTimeFormat('en-SG', {
  timeZone: SG_TIME_ZONE,
  hour: 'numeric',
  hourCycle: 'h23',
})

/** e.g. "18 Oct 2026, 11:12 am" */
export function formatExactTime(iso: string): string {
  return exactFormatter.format(new Date(iso))
}

/** e.g. "11:12 am" */
export function formatClockTime(iso: string): string {
  return timeFormatter.format(new Date(iso))
}

/** e.g. "Sunday, 18 October 2026" */
export function formatLongDate(date: Date): string {
  return longDateFormatter.format(date)
}

/** e.g. "just now", "12 min ago", "3 h ago", "2 days ago" */
export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? '1 day ago' : `${days} days ago`
}

export type DayPart = 'morning' | 'afternoon' | 'evening'

/** Part of the day in Singapore time. */
export function dayPart(date: Date): DayPart {
  const hour = Number(hourFormatter.format(date))
  if (hour < 12) return 'morning'
  if (hour < 18) return 'afternoon'
  return 'evening'
}

/** "Test Resident A" -> "TA" */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  const first = words[0][0] ?? ''
  const last = words.length > 1 ? (words[words.length - 1][0] ?? '') : ''
  return (first + last).toUpperCase()
}
