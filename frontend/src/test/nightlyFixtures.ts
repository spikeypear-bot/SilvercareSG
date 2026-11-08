/**
 * Synthetic `GET /residents/{resident_id}/nightly` bodies, shaped as the API sends them
 * (docs/features/nightly-results.md, API contract). No real resident data.
 */
import type { Night, NightlyOverviewFlagged, NightlyOverviewResponse, NightlyResponse } from '../types/nightly'

/** The spec's example night (18 Nov 2026), overridable field by field. */
export function testNight(overrides: Partial<Night> = {}): Night {
  return {
    date: '2026-11-18',
    hr_bpm_min: 52,
    hr_bpm_avg: 71.4,
    hr_bpm_max: 128,
    spo2_pct_min: 90,
    spo2_pct_avg: 96.1,
    spo2_pct_max: 99,
    skin_temp_c_min: 31.2,
    skin_temp_c_avg: 33.8,
    skin_temp_c_max: 35.0,
    activity_pct_avg: 14.0,
    vitals_readings: 1312,
    hr_bpm_below: 0,
    hr_bpm_above: 12,
    spo2_pct_below: 3,
    skin_temp_c_below: 0,
    skin_temp_c_above: 0,
    thresholds: { hr_bpm: { min: 50, max: 120 }, spo2_pct: { min: 92 }, skin_temp_c: { min: 30.0, max: 38.0 } },
    bed_minutes: 460,
    motion_events: 23,
    falls: 0,
    vitals_alert_episodes: 1,
    cancels: 0,
    ...overrides,
  }
}

export function testNightly(overrides: Partial<NightlyResponse> = {}): NightlyResponse {
  return {
    resident_id: 'res-0003',
    expected_date: '2026-11-18',
    not_ready: false,
    nights: [testNight()],
    ...overrides,
  }
}

/**
 * Synthetic `GET /nightly/overview` bodies (docs/features/nightly-overview.md, API
 * contract). No real resident data.
 */
export function testFlagged(overrides: Partial<NightlyOverviewFlagged> = {}): NightlyOverviewFlagged {
  return {
    resident_id: 'res-0003',
    falls: 0,
    vitals_alert_episodes: 0,
    hr_bpm_above: 0,
    hr_bpm_below: 0,
    spo2_pct_below: 0,
    skin_temp_c_above: 0,
    skin_temp_c_below: 0,
    out_of_limit: 0,
    thresholds: { hr_bpm: { min: 50, max: 120 }, spo2_pct: { min: 92 }, skin_temp_c: { min: 30.0, max: 38.0 } },
    ...overrides,
  }
}

export function testOverview(overrides: Partial<NightlyOverviewResponse> = {}): NightlyOverviewResponse {
  return {
    expected_date: '2026-11-18',
    available: true,
    not_ready: false,
    flagged: [
      testFlagged({ resident_id: 'res-0002', falls: 1 }),
      testFlagged({ resident_id: 'res-0003', vitals_alert_episodes: 1, hr_bpm_above: 12, spo2_pct_below: 3, out_of_limit: 15 }),
    ],
    clear_count: 14,
    no_summary_count: 2,
    ...overrides,
  }
}
