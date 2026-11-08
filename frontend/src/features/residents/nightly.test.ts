/** Formatting helpers for the "Nightly summary" section (docs/features/nightly-results.md, criterion 28). */
import { testNight } from '../../test/nightlyFixtures'
import {
  formatActivityAvg,
  formatBedTime,
  formatCount,
  formatLimit,
  formatLimitsUsed,
  formatNightDate,
  formatReadings,
  sortNightsNewestFirst,
  vitalFigures,
  wornHours,
} from './nightly'

describe('formatNightDate', () => {
  it.each([
    ['2026-11-18', '18 Nov 2026'],
    ['2026-11-01', '1 Nov 2026'],
    ['2026-01-31', '31 Jan 2026'],
    ['2026-12-31', '31 Dec 2026'],
  ])('%s -> %s', (date, label) => {
    expect(formatNightDate(date)).toBe(label)
  })

  it('does not shift the date by time zone (formats the parts, not a Date)', () => {
    // 00:00 in Singapore is the previous day in UTC; the label must stay the 18th.
    expect(formatNightDate('2026-11-18')).toBe('18 Nov 2026')
  })

  it.each(['', '2026-13-01', '2026-00-10', '18/11/2026', '2026-11-18T00:00:00Z'])(
    'returns an unexpected value %j unchanged',
    (value) => {
      expect(formatNightDate(value)).toBe(value)
    },
  )
})

describe('sortNightsNewestFirst', () => {
  it('sorts by date, newest first, without changing the input', () => {
    const input = [testNight({ date: '2026-11-16' }), testNight({ date: '2026-11-18' }), testNight({ date: '2026-10-30' })]
    const sorted = sortNightsNewestFirst(input)
    expect(sorted.map((n) => n.date)).toEqual(['2026-11-18', '2026-11-16', '2026-10-30'])
    expect(input.map((n) => n.date)).toEqual(['2026-11-16', '2026-11-18', '2026-10-30'])
  })
})

describe('counts and readings', () => {
  it('formats counts with thousands separators', () => {
    expect(formatCount(0)).toBe('0')
    expect(formatCount(1312)).toBe('1,312')
  })

  it.each([
    [0, 0],
    [29, 0],
    [30, 1],
    [1289, 21],
    [1290, 22],
    [1312, 22],
    [1440, 24],
  ])('wornHours(%i) = %i (nearest hour)', (readings, hours) => {
    expect(wornHours(readings)).toBe(hours)
  })

  it('formatReadings matches the spec', () => {
    expect(formatReadings(1312)).toBe('Readings 1,312 of 1,440 (worn about 22 h)')
  })
})

describe('formatBedTime and formatActivityAvg', () => {
  it.each([
    [460, '7 h 40 m'],
    [0, '0 h 0 m'],
    [59.6, '1 h 0 m'],
    [null, 'No bed data'],
  ])('formatBedTime(%s) = %s', (minutes, text) => {
    expect(formatBedTime(minutes)).toBe(text)
  })

  it.each([
    [14.0, '14%'],
    [14.4, '14%'],
    [14.5, '15%'],
    [null, 'No readings'],
  ])('formatActivityAvg(%s) = %s', (value, text) => {
    expect(formatActivityAvg(value)).toBe(text)
  })
})

describe('limits', () => {
  it.each([
    [30.0, '30'],
    [37.5, '37.5'],
    [120, '120'],
  ])('formatLimit(%s) = %s', (value, text) => {
    expect(formatLimit(value)).toBe(text)
  })

  it('formatLimitsUsed matches the spec example', () => {
    expect(formatLimitsUsed(testNight().thresholds)).toBe('HR 50–120 · SpO2 ≥92 · 30–38 °C')
  })
})

describe('vitalFigures', () => {
  it('the spec example: HR above flag, SpO2 below flag, skin temp in range', () => {
    expect(vitalFigures(testNight())).toEqual([
      { label: 'Heart rate', stats: 'min 52 avg 71 max 128', flags: ['12 above 120'] },
      { label: 'SpO2', stats: 'min 90 avg 96 max 99', flags: ['3 below 92'] },
      { label: 'Skin temp', stats: 'min 31.2 avg 33.8 max 35.0', flags: [] },
    ])
  })

  it('both flags, above first', () => {
    const [hr] = vitalFigures(testNight({ hr_bpm_above: 2, hr_bpm_below: 5 }))
    expect(hr.flags).toEqual(['2 above 120', '5 below 50'])
  })

  it('null statistics give null stats ("No readings")', () => {
    const [hr] = vitalFigures(testNight({ hr_bpm_min: null, hr_bpm_avg: null, hr_bpm_max: null }))
    expect(hr.stats).toBeNull()
  })
})
