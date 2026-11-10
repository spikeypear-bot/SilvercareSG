/**
 * Formatting for the "Nightly summary" section (docs/features/nightly-results.md,
 * criterion 28). Kept out of NightlySummary.tsx so that file exports only components.
 */
import type { AnomalyReason, Night, NightAnomaly } from '../../types/nightly'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** At most one `vitals` message a minute: 1,440 in a day. */
export const READINGS_PER_DAY = 1440

const countFormatter = new Intl.NumberFormat('en-SG', { maximumFractionDigits: 0 })

/**
 * "2026-11-18" -> "18 Nov 2026". The date is already a Singapore calendar date, so it's
 * formatted from its parts, never through `Date` (which would shift it by time zone).
 */
export function formatNightDate(date: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  if (!match) return date
  const month = MONTHS[Number(match[2]) - 1]
  if (!month) return date
  return `${Number(match[3])} ${month} ${match[1]}`
}

/** Newest first, by `yyyy-mm-dd` date. Doesn't change the input. */
export function sortNightsNewestFirst(nights: readonly Night[]): Night[] {
  return [...nights].sort((a, b) => b.date.localeCompare(a.date))
}

/** 1312 -> "1,312" */
export function formatCount(value: number): string {
  return countFormatter.format(value)
}

/** Hours worn: one reading a minute, rounded to the nearest hour (1,312 -> 22). */
export function wornHours(vitalsReadings: number): number {
  return Math.round(vitalsReadings / 60)
}

/** "Readings 1,312 of 1,440 (worn about 22 h)" */
export function formatReadings(vitalsReadings: number): string {
  return `Readings ${formatCount(vitalsReadings)} of ${formatCount(READINGS_PER_DAY)} (worn about ${wornHours(vitalsReadings)} h)`
}

/** 460 -> "7 h 40 m"; null -> "No bed data". */
export function formatBedTime(minutes: number | null): string {
  if (minutes === null) return 'No bed data'
  const total = Math.max(0, Math.round(minutes))
  return `${Math.floor(total / 60)} h ${total % 60} m`
}

/** Whole number for heart rate and SpO2 (71.4 -> "71"). */
export function formatWhole(value: number): string {
  return String(Math.round(value))
}

/** One decimal for skin temperature (35 -> "35.0"). */
export function formatOneDecimal(value: number): string {
  return value.toFixed(1)
}

/** A limit as written in "Limits used that night" and the flags: 30.0 -> "30", 37.5 -> "37.5". */
export function formatLimit(value: number): string {
  return String(Number(value.toFixed(1)))
}

/** "Activity avg 14%", or "No readings" when null. */
export function formatActivityAvg(value: number | null): string {
  return value === null ? 'No readings' : `${Math.round(value)}%`
}

/** "HR 50–120 · SpO2 ≥92 · 30–38 °C" */
export function formatLimitsUsed(thresholds: Night['thresholds']): string {
  const { hr_bpm, spo2_pct, skin_temp_c } = thresholds
  return (
    `HR ${formatLimit(hr_bpm.min)}–${formatLimit(hr_bpm.max)} · ` +
    `SpO2 ≥${formatLimit(spo2_pct.min)} · ` +
    `${formatLimit(skin_temp_c.min)}–${formatLimit(skin_temp_c.max)} °C`
  )
}

export interface VitalFigures {
  label: string
  /** "min 52 avg 71 max 128", or null when the statistics are null ("No readings"). */
  stats: string | null
  /** Out-of-range flags, e.g. "12 above 120", "3 below 50". Empty for "In range". */
  flags: string[]
}

function stats(
  min: number | null,
  avg: number | null,
  max: number | null,
  format: (value: number) => string,
): string | null {
  if (min === null || avg === null || max === null) return null
  return `min ${format(min)} avg ${format(avg)} max ${format(max)}`
}

function flags(above: number | null, max: number | null, below: number, min: number): string[] {
  const out: string[] = []
  if (above !== null && max !== null && above > 0) out.push(`${formatCount(above)} above ${formatLimit(max)}`)
  if (below > 0) out.push(`${formatCount(below)} below ${formatLimit(min)}`)
  return out
}

/** Heart rate, SpO2 (below only), and skin temperature, compared with the night's own limits. */
export function vitalFigures(night: Night): VitalFigures[] {
  const t = night.thresholds
  return [
    {
      label: 'Heart rate',
      stats: stats(night.hr_bpm_min, night.hr_bpm_avg, night.hr_bpm_max, formatWhole),
      flags: flags(night.hr_bpm_above, t.hr_bpm.max, night.hr_bpm_below, t.hr_bpm.min),
    },
    {
      label: 'SpO2',
      stats: stats(night.spo2_pct_min, night.spo2_pct_avg, night.spo2_pct_max, formatWhole),
      flags: flags(null, null, night.spo2_pct_below, t.spo2_pct.min),
    },
    {
      label: 'Skin temp',
      stats: stats(night.skin_temp_c_min, night.skin_temp_c_avg, night.skin_temp_c_max, formatOneDecimal),
      flags: flags(night.skin_temp_c_above, t.skin_temp_c.max, night.skin_temp_c_below, t.skin_temp_c.min),
    },
  ]
}

/** Scoring needs this many usable baseline nights (docs/features/nightly-anomaly.md). */
export const ANOMALY_MIN_NIGHTS = 20

const REASON_LABELS: Record<AnomalyReason['feature'], string> = {
  hr_bpm_avg: 'HR avg',
  hr_bpm_min: 'HR min',
  hr_bpm_max: 'HR max',
  spo2_pct_avg: 'SpO2 avg',
  spo2_pct_min: 'SpO2 min',
  skin_temp_c_avg: 'Skin temp avg',
  activity_pct_avg: 'Activity avg',
  bed_minutes: 'bed',
  motion_events: 'Motion events',
}

function reasonValue(feature: AnomalyReason['feature'], value: number): string {
  if (feature === 'bed_minutes') return formatBedTime(value)
  if (feature === 'skin_temp_c_avg') return `${formatOneDecimal(value)} °C`
  if (feature.startsWith('spo2') || feature === 'activity_pct_avg') return `${Math.round(value)}%`
  return formatWhole(value)
}

/** "HR avg 78 (usual 58)", or with `separator` ", " as in the Last night list: "HR avg 78, usual 58". */
export function formatAnomalyReason(reason: AnomalyReason, separator: 'brackets' | 'comma' = 'brackets'): string {
  const label = REASON_LABELS[reason.feature]
  const value = reasonValue(reason.feature, reason.value)
  const usual = reasonValue(reason.feature, reason.usual)
  return separator === 'brackets' ? `${label} ${value} (usual ${usual})` : `${label} ${value}, usual ${usual}`
}

export interface AnomalyLine {
  /** Shown with a warning icon and text, never colour alone. */
  unusual: boolean
  text: string
  /** The reasons line, only for an unusual night. */
  detail: string | null
}

/** The "Anomaly check" row of the Nightly summary (docs/features/nightly-anomaly.md, Screens). */
export function anomalyLine(anomaly: NightAnomaly | null): AnomalyLine {
  if (anomaly === null) return { unusual: false, text: 'Not scored for this night', detail: null }
  switch (anomaly.status) {
    case 'low_wear':
      return { unusual: false, text: 'Not scored: low wear time', detail: null }
    case 'not_enough_history':
      return {
        unusual: false,
        text: `Anomaly check needs ${ANOMALY_MIN_NIGHTS} usable nights of history (${formatCount(anomaly.usable_nights)} so far)`,
        detail: null,
      }
    case 'no_model':
      return { unusual: false, text: 'No anomaly model yet', detail: null }
    case 'scored': {
      const figures = `score ${(anomaly.score ?? 0).toFixed(2)}, cut-off ${(anomaly.cutoff ?? 0).toFixed(2)}`
      if (anomaly.unusual) {
        return {
          unusual: true,
          text: `Unusual night for this resident (${figures})`,
          detail: anomaly.reasons.map((r) => formatAnomalyReason(r)).join(' · ') || null,
        }
      }
      return { unusual: false, text: `Usual night (${figures})`, detail: null }
    }
  }
}
