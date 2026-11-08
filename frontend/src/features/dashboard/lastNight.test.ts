/**
 * Text for the doctor dashboard's "Last night" section (docs/features/nightly-overview.md,
 * criteria 19-21).
 */
import { testFlagged } from '../../test/nightlyFixtures'
import { flaggedReasons, flaggedReasonsText, lastNightCountLines, lastNightHeading } from './lastNight'

describe('lastNightHeading', () => {
  it('formats expected_date as on the resident page', () => {
    expect(lastNightHeading('2026-11-18')).toBe('Last night · 18 Nov 2026')
    expect(lastNightHeading('2026-01-05')).toBe('Last night · 5 Jan 2026')
  })
})

describe('flaggedReasons', () => {
  it('gives nothing when every count is 0', () => {
    expect(flaggedReasons(testFlagged())).toEqual([])
  })

  it.each([
    ['falls', 1, '1 fall'],
    ['falls', 2, '2 falls'],
    ['vitals_alert_episodes', 1, '1 alert episode'],
    ['vitals_alert_episodes', 2, '2 alert episodes'],
    ['hr_bpm_above', 12, '12 HR above 120'],
    ['hr_bpm_below', 3, '3 HR below 50'],
    ['spo2_pct_below', 3, '3 SpO2 below 92'],
    ['skin_temp_c_above', 5, '5 skin temp above 38'],
    ['skin_temp_c_below', 5, '5 skin temp below 30'],
  ] as const)('%s = %i reads "%s"', (field, count, text) => {
    expect(flaggedReasons(testFlagged({ [field]: count }))).toEqual([text])
  })

  it('lists every reason in the spec order, joined with " · "', () => {
    const entry = testFlagged({
      falls: 2,
      vitals_alert_episodes: 1,
      hr_bpm_above: 12,
      hr_bpm_below: 3,
      spo2_pct_below: 4,
      skin_temp_c_above: 5,
      skin_temp_c_below: 6,
    })
    expect(flaggedReasonsText(entry)).toBe(
      '2 falls · 1 alert episode · 12 HR above 120 · 3 HR below 50 · 4 SpO2 below 92 · ' +
        '5 skin temp above 38 · 6 skin temp below 30',
    )
  })

  it("uses that night's own limits, shown as on the resident page", () => {
    const entry = testFlagged({
      hr_bpm_above: 1,
      hr_bpm_below: 1,
      spo2_pct_below: 1,
      skin_temp_c_above: 1,
      skin_temp_c_below: 1,
      thresholds: { hr_bpm: { min: 45, max: 110 }, spo2_pct: { min: 88 }, skin_temp_c: { min: 29.5, max: 37.25 } },
    })
    expect(flaggedReasons(entry)).toEqual([
      '1 HR above 110',
      '1 HR below 45',
      '1 SpO2 below 88',
      '1 skin temp above 37.3',
      '1 skin temp below 29.5',
    ])
  })

  it('formats large counts with a thousands separator', () => {
    expect(flaggedReasons(testFlagged({ hr_bpm_above: 1234 }))).toEqual(['1,234 HR above 120'])
  })

  it("says 'limit' for every limit when the night's thresholds are null", () => {
    const entry = testFlagged({
      falls: 1,
      hr_bpm_above: 12,
      hr_bpm_below: 2,
      spo2_pct_below: 3,
      skin_temp_c_above: 4,
      skin_temp_c_below: 5,
      thresholds: null,
    })
    expect(flaggedReasons(entry)).toEqual([
      '1 fall',
      '12 HR above limit',
      '2 HR below limit',
      '3 SpO2 below limit',
      '4 skin temp above limit',
      '5 skin temp below limit',
    ])
  })

  it('says "limit" only for a limit that is missing', () => {
    const entry = testFlagged({
      hr_bpm_above: 1,
      hr_bpm_below: 1,
      thresholds: { hr_bpm: { min: 50 }, spo2_pct: { min: 92 }, skin_temp_c: { min: 30, max: 38 } },
    } as Parameters<typeof testFlagged>[0])
    expect(flaggedReasons(entry)).toEqual(['1 HR above limit', '1 HR below 50'])
  })
})

describe('lastNightCountLines', () => {
  it('gives both lines in plural', () => {
    expect(lastNightCountLines(14, 2)).toEqual([
      '14 residents had nothing flagged.',
      '2 residents have no summary for last night.',
    ])
  })

  it('uses the singular for 1', () => {
    expect(lastNightCountLines(1, 1)).toEqual([
      '1 resident had nothing flagged.',
      '1 resident has no summary for last night.',
    ])
  })

  it('leaves out a line whose count is 0', () => {
    expect(lastNightCountLines(0, 2)).toEqual(['2 residents have no summary for last night.'])
    expect(lastNightCountLines(3, 0)).toEqual(['3 residents had nothing flagged.'])
    expect(lastNightCountLines(0, 0)).toEqual([])
  })
})
