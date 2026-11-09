/**
 * Pure helpers for the vitals trends page (docs/features/vitals-trends.md, criteria
 * 16-19): Singapore dates, the ranges the API accepts, row labels, and the CSV.
 */
import { SG_TIME_ZONE } from '../../lib/format'
import type { VitalsInterval, VitalsRow, VitalsValues } from '../../types/vitals'

/** The API's limits (criteria 3-5). */
export const MAX_DAYS: Record<VitalsInterval, number> = { day: 90, hour: 7 }
export const HOURLY_MAX_AGE_DAYS = 365
export const PRESETS: Record<VitalsInterval, number[]> = { day: [7, 30, 90], hour: [1, 7] }
export const DEFAULT_DAYS: Record<VitalsInterval, number> = { day: 30, hour: 1 }

const sgDate = new Intl.DateTimeFormat('en-CA', {
  timeZone: SG_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

/** `yyyy-mm-dd` plus `days` (may be negative), calendar arithmetic in UTC. */
export function addDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

/** Days from `from` to `to`, both included. */
export function dayCount(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1
}

/** Yesterday's Singapore date: the newest date the API serves. */
export function latestDate(now: number): string {
  return addDays(sgDate.format(new Date(now)), -1)
}

/** The earliest `from` allowed: none for daily, 365 days before today for hourly. */
export function earliestDate(interval: VitalsInterval, now: number): string | undefined {
  return interval === 'hour' ? addDays(latestDate(now), 1 - HOURLY_MAX_AGE_DAYS) : undefined
}

export function presetRange(days: number, now: number): { from: string; to: string } {
  const to = latestDate(now)
  return { from: addDays(to, 1 - days), to }
}

/** Why the API would refuse this range, in words, or null if it is fine. */
export function rangeProblem(interval: VitalsInterval, from: string, to: string, now: number): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to)) return 'Choose a start and end date.'
  if (from > to) return 'The start date must be on or before the end date.'
  if (to > latestDate(now)) return 'Data is available up to yesterday.'
  if (dayCount(from, to) > MAX_DAYS[interval]) return `Choose at most ${MAX_DAYS[interval]} days.`
  const earliest = earliestDate(interval, now)
  if (earliest && from < earliest) return 'Hourly data covers the last 365 days.'
  return null
}

const hourFormat = new Intl.DateTimeFormat('en-SG', {
  timeZone: SG_TIME_ZONE,
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})
const dayFormat = new Intl.DateTimeFormat('en-SG', {
  timeZone: 'UTC',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

/** The row's label in Singapore time: "18 Nov, 14:00" or "18 Nov 2026". */
export function rowLabel(row: VitalsRow): string {
  if (row.hour) return hourFormat.format(new Date(row.hour))
  return row.date ? dayFormat.format(new Date(`${row.date}T00:00:00Z`)) : ''
}

/** Table and CSV columns, in the API's order, with headings. */
export const VALUE_COLUMNS: Array<{ key: keyof VitalsValues; label: string }> = [
  { key: 'hr_bpm_min', label: 'Heart rate min (bpm)' },
  { key: 'hr_bpm_avg', label: 'Heart rate avg (bpm)' },
  { key: 'hr_bpm_max', label: 'Heart rate max (bpm)' },
  { key: 'spo2_pct_min', label: 'SpO2 min (%)' },
  { key: 'spo2_pct_avg', label: 'SpO2 avg (%)' },
  { key: 'spo2_pct_max', label: 'SpO2 max (%)' },
  { key: 'skin_temp_c_min', label: 'Skin temp min (°C)' },
  { key: 'skin_temp_c_avg', label: 'Skin temp avg (°C)' },
  { key: 'skin_temp_c_max', label: 'Skin temp max (°C)' },
  { key: 'activity_pct_avg', label: 'Activity avg (%)' },
  { key: 'vitals_readings', label: 'Readings' },
  { key: 'hr_bpm_below', label: 'Heart rate below limit' },
  { key: 'hr_bpm_above', label: 'Heart rate above limit' },
  { key: 'spo2_pct_below', label: 'SpO2 below limit' },
  { key: 'skin_temp_c_below', label: 'Skin temp below limit' },
  { key: 'skin_temp_c_above', label: 'Skin temp above limit' },
  { key: 'bed_minutes', label: 'In bed (min)' },
  { key: 'motion_events', label: 'Motion events' },
  { key: 'falls', label: 'Falls' },
  { key: 'vitals_alert_episodes', label: 'Vitals alert episodes' },
  { key: 'cancels', label: '"I\'m OK" cancels' },
]

/** Readings outside the limits in a row (heart rate, SpO2, skin temperature). */
export function outOfLimit(row: VitalsValues): number {
  return (
    (row.hr_bpm_below ?? 0) +
    (row.hr_bpm_above ?? 0) +
    (row.spo2_pct_below ?? 0) +
    (row.skin_temp_c_below ?? 0) +
    (row.skin_temp_c_above ?? 0)
  )
}

function csvCell(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  // Quote anything with a separator, quote, or newline; a leading = + - @ is prefixed
  // with ' so a spreadsheet never runs it as a formula.
  const safe = /^[=+\-@]/.test(text) && typeof value === 'string' ? `'${text}` : text
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe
}

/** The rows as CSV (criterion 19): opaque resident ID only, never a name. */
export function toCsv(residentId: string, interval: VitalsInterval, rows: VitalsRow[]): string {
  const first = interval === 'hour' ? 'hour' : 'date'
  const header = ['resident_id', first, 'has_data', ...VALUE_COLUMNS.map((c) => c.key)]
  const lines = rows.map((row) =>
    [residentId, interval === 'hour' ? row.hour : row.date, row.has_data, ...VALUE_COLUMNS.map((c) => row[c.key])]
      .map(csvCell)
      .join(','),
  )
  return [header.join(','), ...lines].join('\r\n') + '\r\n'
}

export function csvFilename(residentId: string, interval: VitalsInterval, from: string, to: string): string {
  return `vitals-${residentId}-${interval}-${from}-${to}.csv`
}
