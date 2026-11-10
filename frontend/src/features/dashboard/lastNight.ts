/**
 * Text for the doctor dashboard's "Last night" section (docs/features/nightly-overview.md,
 * criteria 19-21). Kept out of LastNightSection.tsx so that file exports only components.
 */
import type { NightlyOverviewFlagged } from '../../types/nightly'
import { formatAnomalyReason, formatCount, formatLimit, formatNightDate } from '../residents/nightly'

/** "Last night · 18 Nov 2026" (the date as on the resident page's Nightly summary). */
export function lastNightHeading(expectedDate: string): string {
  return `Last night · ${formatNightDate(expectedDate)}`
}

function plural(count: number, one: string, many: string): string {
  return `${formatCount(count)} ${count === 1 ? one : many}`
}

/**
 * The reasons a night was flagged, in the spec's order, each only when its count is above
 * 0, compared with that night's own limits: e.g. ["1 fall", "12 HR above 120"]. When
 * the night's limits couldn't be read (`thresholds: null`), or one limit is missing, the
 * reason says "limit" instead of the number ("12 HR above limit"), so the night still shows.
 */
export function flaggedReasons(entry: NightlyOverviewFlagged): string[] {
  const t = entry.thresholds
  const limit = (value: number | undefined) => (typeof value === 'number' ? formatLimit(value) : 'limit')
  const out: string[] = []
  if (entry.falls > 0) out.push(plural(entry.falls, 'fall', 'falls'))
  if (entry.vitals_alert_episodes > 0) {
    out.push(plural(entry.vitals_alert_episodes, 'alert episode', 'alert episodes'))
  }
  if (entry.hr_bpm_above > 0) out.push(`${formatCount(entry.hr_bpm_above)} HR above ${limit(t?.hr_bpm?.max)}`)
  if (entry.hr_bpm_below > 0) out.push(`${formatCount(entry.hr_bpm_below)} HR below ${limit(t?.hr_bpm?.min)}`)
  if (entry.spo2_pct_below > 0) {
    out.push(`${formatCount(entry.spo2_pct_below)} SpO2 below ${limit(t?.spo2_pct?.min)}`)
  }
  if (entry.skin_temp_c_above > 0) {
    out.push(`${formatCount(entry.skin_temp_c_above)} skin temp above ${limit(t?.skin_temp_c?.max)}`)
  }
  if (entry.skin_temp_c_below > 0) {
    out.push(`${formatCount(entry.skin_temp_c_below)} skin temp below ${limit(t?.skin_temp_c?.min)}`)
  }
  // Last, the softest signal (docs/features/nightly-anomaly.md, criterion 20).
  if (entry.unusual) {
    const why = entry.anomaly_reasons.map((r) => formatAnomalyReason(r, 'comma')).join(' · ')
    out.push(why ? `unusual night (${why})` : 'unusual night')
  }
  return out
}

/** "1 fall · 1 alert episode · 12 HR above 120" */
export function flaggedReasonsText(entry: NightlyOverviewFlagged): string {
  return flaggedReasons(entry).join(' · ')
}

/** The lines under the list, each only when its count is above 0. */
export function lastNightCountLines(clearCount: number, noSummaryCount: number): string[] {
  const lines: string[] = []
  if (clearCount > 0) {
    lines.push(
      clearCount === 1
        ? '1 resident had nothing flagged.'
        : `${formatCount(clearCount)} residents had nothing flagged.`,
    )
  }
  if (noSummaryCount > 0) {
    lines.push(
      noSummaryCount === 1
        ? '1 resident has no summary for last night.'
        : `${formatCount(noSummaryCount)} residents have no summary for last night.`,
    )
  }
  return lines
}
