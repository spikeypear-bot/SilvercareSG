import type { ThresholdKey } from '../../types/resident'
import { DEMO_DEFAULT_THRESHOLDS } from '../dashboard/demoData'
import {
  THRESHOLD_FIELDS,
  THRESHOLD_INPUT_RANGES,
  formatThresholdValue,
  thresholdName,
  thresholdsToForm,
  validateThresholds,
  type ThresholdField,
  type ThresholdForm,
} from './thresholds'

const VALID: ThresholdForm = { hrMin: '50', hrMax: '110', spo2Min: '92', tempMin: '31.0', tempMax: '36.0' }

const withField = (field: ThresholdField, value: string): ThresholdForm => ({ ...VALID, [field]: value })

function errorsFor(form: ThresholdForm) {
  const result = validateThresholds(form)
  if (result.ok) throw new Error('expected invalid')
  return result.errors
}

describe('THRESHOLD_INPUT_RANGES and THRESHOLD_FIELDS', () => {
  it('match the metrics.md physically possible ranges', () => {
    expect(THRESHOLD_INPUT_RANGES).toEqual({
      hr_bpm: { min: 20, max: 250 },
      spo2_pct: { min: 50, max: 100 },
      skin_temp_c: { min: 20, max: 45 },
    })
  })

  it('lists the five fields in display order with labels and units', () => {
    expect(THRESHOLD_FIELDS.map((f) => f.field)).toEqual(['hrMin', 'hrMax', 'spo2Min', 'tempMin', 'tempMax'])
    THRESHOLD_FIELDS.forEach((f) => expect(f.label).toMatch(/\((bpm|%|°C)\)$/))
  })

  it('gives each field its history key, name and unit; labels are unchanged', () => {
    expect(THRESHOLD_FIELDS.map(({ field, key, name, unit, label }) => ({ field, key, name, unit, label }))).toEqual([
      {
        field: 'hrMin',
        key: 'hr_bpm.min',
        name: 'Heart rate, lower limit',
        unit: 'bpm',
        label: 'Heart rate, lower limit (bpm)',
      },
      {
        field: 'hrMax',
        key: 'hr_bpm.max',
        name: 'Heart rate, upper limit',
        unit: 'bpm',
        label: 'Heart rate, upper limit (bpm)',
      },
      { field: 'spo2Min', key: 'spo2_pct.min', name: 'SpO2, lower limit', unit: '%', label: 'SpO2, lower limit (%)' },
      {
        field: 'tempMin',
        key: 'skin_temp_c.min',
        name: 'Skin temperature, lower limit',
        unit: '°C',
        label: 'Skin temperature, lower limit (°C)',
      },
      {
        field: 'tempMax',
        key: 'skin_temp_c.max',
        name: 'Skin temperature, upper limit',
        unit: '°C',
        label: 'Skin temperature, upper limit (°C)',
      },
    ])
  })

  it('each key matches its metric', () => {
    THRESHOLD_FIELDS.forEach((f) => expect(f.key.startsWith(`${f.metric}.`)).toBe(true))
  })
})

describe('formatThresholdValue', () => {
  it.each([
    ['hr_bpm.min', 50, '50 bpm'],
    ['hr_bpm.max', 110, '110 bpm'],
    ['spo2_pct.min', 88, '88%'],
    ['spo2_pct.min', 100, '100%'],
    ['skin_temp_c.min', 31, '31.0 °C'],
    ['skin_temp_c.min', 30.5, '30.5 °C'],
    ['skin_temp_c.max', 36, '36.0 °C'],
  ] as const)('%s %d -> "%s"', (key, value, text) => {
    expect(formatThresholdValue(key, value)).toBe(text)
  })

  it('rounds to the field precision', () => {
    expect(formatThresholdValue('hr_bpm.max', 110.4)).toBe('110 bpm')
    expect(formatThresholdValue('skin_temp_c.max', 36.04)).toBe('36.0 °C')
  })

  it('falls back to the bare number for an unknown key', () => {
    expect(formatThresholdValue('unknown.key' as ThresholdKey, 7)).toBe('7')
  })
})

describe('thresholdName', () => {
  it.each([
    ['hr_bpm.min', 'Heart rate, lower limit'],
    ['hr_bpm.max', 'Heart rate, upper limit'],
    ['spo2_pct.min', 'SpO2, lower limit'],
    ['skin_temp_c.min', 'Skin temperature, lower limit'],
    ['skin_temp_c.max', 'Skin temperature, upper limit'],
  ] as const)('%s -> "%s"', (key, name) => {
    expect(thresholdName(key)).toBe(name)
  })

  it('falls back to the key itself for an unknown key', () => {
    expect(thresholdName('unknown.key' as ThresholdKey)).toBe('unknown.key')
  })
})

describe('validateThresholds', () => {
  it('returns the parsed VitalsThresholds for valid input', () => {
    expect(validateThresholds(VALID)).toEqual({ ok: true, values: DEMO_DEFAULT_THRESHOLDS })
  })

  it('trims surrounding whitespace and accepts whole-number temperatures', () => {
    const result = validateThresholds({ hrMin: ' 45 ', hrMax: '120', spo2Min: '88', tempMin: '30', tempMax: '37.5' })
    expect(result).toEqual({
      ok: true,
      values: { hr_bpm: { min: 45, max: 120 }, spo2_pct: { min: 88 }, skin_temp_c: { min: 30, max: 37.5 } },
    })
  })

  it.each(THRESHOLD_FIELDS.map((f) => f.field))('empty %s -> "Enter a value."', (field) => {
    expect(errorsFor(withField(field, ''))).toEqual({ [field]: 'Enter a value.' })
    expect(errorsFor(withField(field, '   '))).toEqual({ [field]: 'Enter a value.' })
  })

  it.each([
    ['hrMin', '60.5'],
    ['hrMax', '100.0'],
    ['spo2Min', '92.1'],
    ['hrMin', 'abc'],
    ['spo2Min', '9 2'],
    ['hrMax', '1e2'],
  ] as const)('non-integer %s "%s" -> "Enter a whole number."', (field, value) => {
    expect(errorsFor(withField(field, value))).toEqual({ [field]: 'Enter a whole number.' })
  })

  it.each([
    ['tempMin', '31.05'],
    ['tempMax', '36.25'],
    ['tempMin', 'warm'],
    ['tempMax', '36.'],
  ] as const)('temperature %s "%s" -> "Enter a number with at most 1 decimal place."', (field, value) => {
    expect(errorsFor(withField(field, value))).toEqual({
      [field]: 'Enter a number with at most 1 decimal place.',
    })
  })

  it.each([
    ['hrMin', '19', 20, 250],
    ['hrMax', '251', 20, 250],
    ['hrMin', '-5', 20, 250],
    ['spo2Min', '49', 50, 100],
    ['spo2Min', '101', 50, 100],
    ['tempMin', '19.9', 20, 45],
    ['tempMax', '45.1', 20, 45],
  ] as const)('out of range %s "%s" -> "Enter a value from %i to %i."', (field, value, min, max) => {
    const errors = errorsFor(withField(field, value))
    expect(errors[field]).toBe(`Enter a value from ${min} to ${max}.`)
  })

  it.each([
    ['hrMin', '20'],
    ['hrMax', '250'],
    ['spo2Min', '50'],
    ['spo2Min', '100'],
    ['tempMin', '20.0'],
    ['tempMax', '45'],
  ] as const)('accepts the range boundary %s "%s"', (field, value) => {
    expect(validateThresholds(withField(field, value)).ok).toBe(true)
  })

  it.each([
    ['hrMin', 'hrMax', '110', '110'],
    ['hrMin', 'hrMax', '120', '110'],
    ['tempMin', 'tempMax', '36.0', '36.0'],
    ['tempMin', 'tempMax', '37.0', '36.0'],
  ] as const)('%s >= %s puts "Must be higher than the lower limit." on the max field', (minField, maxField, min, max) => {
    const errors = errorsFor({ ...VALID, [minField]: min, [maxField]: max })
    expect(errors).toEqual({ [maxField]: 'Must be higher than the lower limit.' })
  })

  it('does not compare min and max when one of them is already invalid', () => {
    expect(errorsFor({ ...VALID, hrMin: 'x', hrMax: '30' })).toEqual({ hrMin: 'Enter a whole number.' })
  })

  it('reports every invalid field at once', () => {
    const errors = errorsFor({ hrMin: '', hrMax: '7.5', spo2Min: '200', tempMin: '31.25', tempMax: '' })
    expect(errors).toEqual({
      hrMin: 'Enter a value.',
      hrMax: 'Enter a whole number.',
      spo2Min: 'Enter a value from 50 to 100.',
      tempMin: 'Enter a number with at most 1 decimal place.',
      tempMax: 'Enter a value.',
    })
  })
})

describe('thresholdsToForm', () => {
  it('formats HR and SpO2 as whole numbers and temperature with 1 decimal place', () => {
    expect(thresholdsToForm(DEMO_DEFAULT_THRESHOLDS)).toEqual(VALID)
  })

  it('round-trips through validateThresholds', () => {
    const samples = [
      DEMO_DEFAULT_THRESHOLDS,
      { hr_bpm: { min: 50, max: 120 }, spo2_pct: { min: 88 }, skin_temp_c: { min: 30.5, max: 37.2 } },
      { hr_bpm: { min: 20, max: 250 }, spo2_pct: { min: 100 }, skin_temp_c: { min: 20, max: 45 } },
    ]
    samples.forEach((t) => expect(validateThresholds(thresholdsToForm(t))).toEqual({ ok: true, values: t }))
  })
})
