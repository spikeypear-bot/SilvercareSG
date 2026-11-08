/**
 * `GET /residents/{resident_id}/nightly` (doctor only; docs/features/nightly-results.md,
 * API contract). One stored night per resident per Singapore date, up to 30, from the
 * `silvercare-results` table. Field names follow the curated-layer summary
 * (docs/features/curated-layer.md, criterion 11).
 */

/** The resolved limits the summaries job used for that night. */
export interface NightThresholds {
  hr_bpm: { min: number; max: number }
  spo2_pct: { min: number }
  skin_temp_c: { min: number; max: number }
}

export interface Night {
  /** Singapore date, `yyyy-mm-dd`. */
  date: string
  /** Statistics are null when there was no non-null reading that night. */
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
  /** `vitals` messages used: at most 1,440 (one a minute while worn). */
  vitals_readings: number
  hr_bpm_below: number
  hr_bpm_above: number
  /** SpO2 has no max, so no `spo2_pct_above`. */
  spo2_pct_below: number
  skin_temp_c_below: number
  skin_temp_c_above: number
  thresholds: NightThresholds
  /** null when there was no bed data. */
  bed_minutes: number | null
  motion_events: number
  falls: number
  vitals_alert_episodes: number
  cancels: number
}

export interface NightlyResponse {
  resident_id: string
  /** Yesterday's Singapore date, `yyyy-mm-dd`. */
  expected_date: string
  /** true only at 09:30 SGT or later when the newest night isn't `expected_date`. */
  not_ready: boolean
  /** Newest first. */
  nights: Night[]
}
