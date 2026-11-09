import { describe, expect, it } from 'vitest'
import type { VitalsRow } from '../../types/vitals'
import {
  addDays,
  csvFilename,
  dayCount,
  earliestDate,
  latestDate,
  presetRange,
  rangeProblem,
  rowLabel,
  toCsv,
} from './vitalsTrends'

// 2026-11-19 00:30 SGT = 2026-11-18 16:30 UTC: today in Singapore is the 19th.
const NOW = Date.parse('2026-11-18T16:30:00Z')

function row(overrides: Partial<VitalsRow>): VitalsRow {
  return {
    has_data: true,
    hr_bpm_min: null,
    hr_bpm_avg: null,
    hr_bpm_max: null,
    spo2_pct_min: null,
    spo2_pct_avg: null,
    spo2_pct_max: null,
    skin_temp_c_min: null,
    skin_temp_c_avg: null,
    skin_temp_c_max: null,
    activity_pct_avg: null,
    vitals_readings: 0,
    hr_bpm_below: 0,
    hr_bpm_above: 0,
    spo2_pct_below: 0,
    skin_temp_c_below: 0,
    skin_temp_c_above: 0,
    bed_minutes: null,
    motion_events: 0,
    falls: 0,
    vitals_alert_episodes: 0,
    cancels: 0,
    ...overrides,
  }
}

describe('dates', () => {
  it('uses the Singapore date for yesterday', () => {
    expect(latestDate(NOW)).toBe('2026-11-18')
  })

  it('adds days across months and counts both ends', () => {
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
    expect(dayCount('2026-11-12', '2026-11-18')).toBe(7)
  })

  it('presets end yesterday', () => {
    expect(presetRange(30, NOW)).toEqual({ from: '2026-10-20', to: '2026-11-18' })
  })

  it('hourly starts at most 365 days back; daily has no limit', () => {
    expect(earliestDate('hour', NOW)).toBe('2025-11-19')
    expect(earliestDate('day', NOW)).toBeUndefined()
  })
})

describe('rangeProblem', () => {
  it.each([
    ['day', '2026-08-21', '2026-11-18', null],
    ['day', '2026-08-20', '2026-11-18', 'Choose at most 90 days.'],
    ['hour', '2026-11-12', '2026-11-18', null],
    ['hour', '2026-11-11', '2026-11-18', 'Choose at most 7 days.'],
    ['day', '2026-11-18', '2026-11-19', 'Data is available up to yesterday.'],
    ['day', '2026-11-18', '2026-11-17', 'The start date must be on or before the end date.'],
    ['hour', '2025-11-18', '2025-11-18', 'Hourly data covers the last 365 days.'],
    ['day', '', '2026-11-18', 'Choose a start and end date.'],
  ] as const)('%s %s..%s', (interval, from, to, expected) => {
    expect(rangeProblem(interval, from, to, NOW)).toBe(expected)
  })
})

describe('labels', () => {
  it('shows hours in Singapore time and dates as given', () => {
    expect(rowLabel(row({ hour: '2026-11-18T14:00:00+08:00' }))).toMatch(/18 Nov.*14:00/)
    expect(rowLabel(row({ date: '2026-11-18' }))).toBe('18 Nov 2026')
  })
})

describe('toCsv', () => {
  it('has a header and one line per row, resident ID only', () => {
    const csv = toCsv('res-0003', 'day', [
      row({ date: '2026-11-17', hr_bpm_avg: 71.4, bed_minutes: 512 }),
      row({ date: '2026-11-18', has_data: false, vitals_readings: null }),
    ])
    const lines = csv.trimEnd().split('\r\n')
    expect(lines).toHaveLength(3)
    expect(lines[0].startsWith('resident_id,date,has_data,hr_bpm_min,hr_bpm_avg')).toBe(true)
    expect(lines[1]).toContain('res-0003,2026-11-17,true,,71.4,')
    expect(lines[2]).toContain('res-0003,2026-11-18,false,')
  })

  it('names the file after the opaque ID, interval, and dates', () => {
    expect(csvFilename('res-0003', 'hour', '2026-11-18', '2026-11-18')).toBe(
      'vitals-res-0003-hour-2026-11-18-2026-11-18.csv',
    )
  })
})
