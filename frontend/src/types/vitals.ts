/**
 * `GET /residents/{resident_id}/vitals` (doctor only; docs/features/vitals-trends.md,
 * API contract). One row per Singapore hour or day. Field names follow the curated-layer
 * summary (docs/features/curated-layer.md, criterion 11).
 */
import type { NightThresholds } from './nightly'

export type VitalsInterval = 'hour' | 'day'

export interface VitalsValues {
  hr_bpm_min: number | null
  hr_bpm_avg: number | null
  hr_bpm_max: number | null
  spo2_pct_min: number | null
  spo2_pct_avg: number | null
  spo2_pct_max: number | null
  skin_temp_c_min: number | null
  skin_temp_c_avg: number | null
  skin_temp_c_max: number | null
  activity_pct_avg: number | null
  vitals_readings: number | null
  hr_bpm_below: number | null
  hr_bpm_above: number | null
  spo2_pct_below: number | null
  skin_temp_c_below: number | null
  skin_temp_c_above: number | null
  /** null when the bed state is unknown (no bedroom sensor or no bed event). */
  bed_minutes: number | null
  motion_events: number | null
  falls: number | null
  vitals_alert_episodes: number | null
  cancels: number | null
}

export interface VitalsRow extends VitalsValues {
  /** Daily rows: Singapore date `yyyy-mm-dd`. */
  date?: string
  /** Hourly rows: ISO 8601 with `+08:00`. */
  hour?: string
  /** false: no summary (daily) or no readings (hourly); every value is null or 0. */
  has_data: boolean
  /** Daily rows: the limits stored with that day's summary; null without one. */
  thresholds?: Partial<NightThresholds> | null
}

export interface VitalsResponse {
  resident_id: string
  interval: VitalsInterval
  from: string
  to: string
  /** Hourly: the current resolved limits used for the counts. Daily: null. */
  thresholds: NightThresholds | null
  rows: VitalsRow[]
}
