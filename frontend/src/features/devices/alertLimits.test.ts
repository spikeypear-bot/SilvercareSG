import {
  ALERT_LIMITS_STATUS_LABELS,
  FIXED_DEVICE_LIMITS,
  formatDeviceLimits,
  parseAlertLimits,
  parseDeviceLimits,
  wearableLimits,
  wearableLimitsSentence,
} from './alertLimits'

const valid = () => ({ hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 88 } })

describe('parseDeviceLimits (spec, Valid limits)', () => {
  it('accepts valid limits and returns a copy', () => {
    const input = valid()
    const parsed = parseDeviceLimits(input)
    expect(parsed).toEqual(valid())
    expect(parsed).not.toBe(input)
    expect(parsed?.hr_bpm).not.toBe(input.hr_bpm)
  })

  it.each([
    ['hr min 45 (lowest)', { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 88 } }],
    ['hr max 130 (highest)', { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 100 } }],
    ['hr min one below max', { hr_bpm: { min: 60, max: 61 }, spo2_pct: { min: 95 } }],
  ])('accepts boundary values: %s', (_name, input) => {
    expect(parseDeviceLimits(input)).toEqual(input)
  })

  it.each([
    ['non-integer hr min', { hr_bpm: { min: 45.5, max: 130 }, spo2_pct: { min: 88 } }],
    ['non-integer spo2', { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 90.1 } }],
    ['string number', { hr_bpm: { min: '45', max: 130 }, spo2_pct: { min: 88 } }],
    ['NaN', { hr_bpm: { min: Number.NaN, max: 130 }, spo2_pct: { min: 88 } }],
    ['boolean', { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: true } }],
    ['hr min 44 (below 45)', { hr_bpm: { min: 44, max: 130 }, spo2_pct: { min: 88 } }],
    ['hr max 131 (above 130)', { hr_bpm: { min: 45, max: 131 }, spo2_pct: { min: 88 } }],
    ['hr max 19 (below 20)', { hr_bpm: { min: 45, max: 19 }, spo2_pct: { min: 88 } }],
    ['spo2 87 (below 88)', { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 87 } }],
    ['spo2 101 (above 100)', { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 101 } }],
    ['min equal to max', { hr_bpm: { min: 100, max: 100 }, spo2_pct: { min: 88 } }],
    ['min above max', { hr_bpm: { min: 120, max: 100 }, spo2_pct: { min: 88 } }],
    ['extra top-level key', { ...valid(), skin_temp_c: { min: 30, max: 38 } }],
    ['extra hr key', { hr_bpm: { min: 45, max: 130, avg: 80 }, spo2_pct: { min: 88 } }],
    ['extra spo2 key', { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 88, max: 100 } }],
    ['missing spo2', { hr_bpm: { min: 45, max: 130 } }],
    ['missing hr max', { hr_bpm: { min: 45 }, spo2_pct: { min: 88 } }],
    ['hr as array', { hr_bpm: [45, 130], spo2_pct: { min: 88 } }],
    ['null', null],
    ['undefined', undefined],
    ['array', [valid()]],
    ['string', 'HR 45-130'],
  ])('rejects %s -> null', (_name, input) => {
    expect(parseDeviceLimits(input)).toBeNull()
  })
})

describe('parseAlertLimits', () => {
  it('keeps staff desired and reported, parsed', () => {
    const desired = { hr_bpm: { min: 45, max: 110 }, spo2_pct: { min: 88 } }
    expect(parseAlertLimits({ status: 'pending', desired, reported: valid() })).toEqual({
      status: 'pending',
      desired,
      reported: valid(),
    })
  })

  it('turns invalid desired/reported into null, keeping the key', () => {
    const parsed = parseAlertLimits({ status: 'unknown', desired: { hr_bpm: 1 }, reported: null })
    expect(parsed).toEqual({ status: 'unknown', desired: null, reported: null })
    expect(parsed).toHaveProperty('desired', null)
    expect(parsed).toHaveProperty('reported', null)
  })

  it('an ops response ({status} only) gets no desired or reported keys', () => {
    const parsed = parseAlertLimits({ status: 'in_sync' })
    expect(parsed).toEqual({ status: 'in_sync' })
    expect(parsed).not.toHaveProperty('desired')
    expect(parsed).not.toHaveProperty('reported')
  })

  it.each(['in_sync', 'pending', 'unknown'] as const)('keeps status %s', (status) => {
    expect(parseAlertLimits({ status })?.status).toBe(status)
  })

  it.each([['stale'], ['IN_SYNC'], [''], [null], [1], [undefined]])('reads unknown status %j as unknown', (status) => {
    expect(parseAlertLimits({ status })?.status).toBe('unknown')
  })

  it('reads a missing status as unknown', () => {
    expect(parseAlertLimits({})).toEqual({ status: 'unknown' })
  })

  it('drops keys it does not know', () => {
    expect(parseAlertLimits({ status: 'in_sync', shadow_version: 7 })).toEqual({ status: 'in_sync' })
  })

  it.each([[null], [undefined], ['pending'], [[{ status: 'pending' }]]])('returns null for non-object %j', (value) => {
    expect(parseAlertLimits(value)).toBeNull()
  })
})

describe('formatDeviceLimits and labels', () => {
  it('formats as in criterion 16', () => {
    expect(formatDeviceLimits(valid())).toBe('HR 45-130 bpm · SpO₂ ≥ 88 %')
    expect(formatDeviceLimits({ hr_bpm: { min: 45, max: 110 }, spo2_pct: { min: 92 } })).toBe(
      'HR 45-110 bpm · SpO₂ ≥ 92 %',
    )
  })

  it('has the status texts from criterion 16', () => {
    expect(ALERT_LIMITS_STATUS_LABELS).toEqual({
      in_sync: 'Limits up to date',
      pending: 'Waiting for device',
      unknown: 'Status unknown',
    })
  })

  it('fixed limits are 45 / 130 / 88', () => {
    expect(FIXED_DEVICE_LIMITS).toEqual(valid())
  })
})

describe('wearableLimits (spec, Computing a wearable\'s limits)', () => {
  const t = (hrMin: number, hrMax: number, spo2: number) => ({
    hr_bpm: { min: hrMin, max: hrMax },
    spo2_pct: { min: spo2 },
    skin_temp_c: { min: 30, max: 38 },
  })

  it('keeps thresholds stricter than 45 / 130 / 88', () => {
    expect(wearableLimits(t(50, 120, 92))).toEqual({ hr_bpm: { min: 50, max: 120 }, spo2_pct: { min: 92 } })
  })

  it('uses 45 / 130 / 88 where thresholds are looser', () => {
    expect(wearableLimits(t(30, 180, 80))).toEqual(valid())
  })

  it('a res-0007-like hr max of 140 becomes 130', () => {
    expect(wearableLimits(t(50, 140, 92))).toEqual({ hr_bpm: { min: 50, max: 130 }, spo2_pct: { min: 92 } })
  })

  it('falls back to 45 / 130 for heart rate when min >= max, keeping the computed SpO2', () => {
    // min 135 -> 135, max 140 -> 130: min > max.
    expect(wearableLimits(t(135, 140, 95))).toEqual({ hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 95 } })
    // min 130, max 200 -> 130: min == max.
    expect(wearableLimits(t(130, 200, 85))).toEqual(valid())
  })

  it('never includes skin temperature, and the result passes the validity check', () => {
    for (const th of [t(50, 120, 92), t(30, 180, 80), t(135, 140, 95), t(130, 200, 100)]) {
      const limits = wearableLimits(th)
      expect(Object.keys(limits).sort()).toEqual(['hr_bpm', 'spo2_pct'])
      expect(parseDeviceLimits(limits)).toEqual(limits)
    }
  })

  it('does not share the fixed limits object (callers cannot mutate it)', () => {
    const limits = wearableLimits(t(135, 140, 95))
    expect(limits.hr_bpm).not.toBe(FIXED_DEVICE_LIMITS.hr_bpm)
  })

  it('builds the criterion 18 sentence', () => {
    expect(wearableLimitsSentence(wearableLimits(t(50, 140, 92)))).toBe(
      'The wearable sends a critical alert below 50 or above 130 bpm, or SpO₂ below 92 %.',
    )
  })
})
