import type { ThresholdKey, VitalsThresholds } from '../../types/resident'

/**
 * Accepted values: the "physically possible" valid ranges of the `vitals` message in
 * docs/metrics.md. Heart rate and SpO2 are whole numbers, skin temperature has 1 decimal
 * place, and each lower limit is below its upper limit. The API will check the same.
 */
export const THRESHOLD_INPUT_RANGES = {
  hr_bpm: { min: 20, max: 250 },
  spo2_pct: { min: 50, max: 100 },
  skin_temp_c: { min: 20, max: 45 },
} as const

export type ThresholdField = 'hrMin' | 'hrMax' | 'spo2Min' | 'tempMin' | 'tempMax'
export type ThresholdForm = Record<ThresholdField, string>
export type ThresholdErrors = Partial<Record<ThresholdField, string>>

type Metric = keyof typeof THRESHOLD_INPUT_RANGES

interface FieldSpec {
  field: ThresholdField
  /** Name in the API and the change history (docs/data-model.md pattern 25). */
  key: ThresholdKey
  metric: Metric
  /** Form label, with the unit. */
  label: string
  /** "Heart rate, upper limit", without the unit. */
  name: string
  unit: string
  decimals: 0 | 1
}

/** Form fields in display order. */
export const THRESHOLD_FIELDS: readonly FieldSpec[] = [
  field('hrMin', 'hr_bpm.min', 'hr_bpm', 'Heart rate, lower limit', 'bpm', 0),
  field('hrMax', 'hr_bpm.max', 'hr_bpm', 'Heart rate, upper limit', 'bpm', 0),
  field('spo2Min', 'spo2_pct.min', 'spo2_pct', 'SpO2, lower limit', '%', 0),
  field('tempMin', 'skin_temp_c.min', 'skin_temp_c', 'Skin temperature, lower limit', '°C', 1),
  field('tempMax', 'skin_temp_c.max', 'skin_temp_c', 'Skin temperature, upper limit', '°C', 1),
]

function field(
  f: ThresholdField,
  key: ThresholdKey,
  metric: Metric,
  name: string,
  unit: string,
  decimals: 0 | 1,
): FieldSpec {
  return { field: f, key, metric, label: `${name} (${unit})`, name, unit, decimals }
}

/** "110 bpm", "31.0 °C", "88%" */
export function formatThresholdValue(key: ThresholdKey, value: number): string {
  const spec = THRESHOLD_FIELDS.find((f) => f.key === key)
  if (!spec) return String(value)
  const text = value.toFixed(spec.decimals)
  return spec.unit === '%' ? `${text}%` : `${text} ${spec.unit}`
}

export function thresholdName(key: ThresholdKey): string {
  return THRESHOLD_FIELDS.find((f) => f.key === key)?.name ?? key
}

export function thresholdsToForm(t: VitalsThresholds): ThresholdForm {
  return {
    hrMin: String(t.hr_bpm.min),
    hrMax: String(t.hr_bpm.max),
    spo2Min: String(t.spo2_pct.min),
    tempMin: t.skin_temp_c.min.toFixed(1),
    tempMax: t.skin_temp_c.max.toFixed(1),
  }
}

function parseField(raw: string, spec: FieldSpec): number | string {
  const text = raw.trim()
  if (text === '') return 'Enter a value.'
  const pattern = spec.decimals === 0 ? /^-?\d+$/ : /^-?\d+(\.\d)?$/
  if (!pattern.test(text)) {
    return spec.decimals === 0 ? 'Enter a whole number.' : 'Enter a number with at most 1 decimal place.'
  }
  const value = Number(text)
  const range = THRESHOLD_INPUT_RANGES[spec.metric]
  if (value < range.min || value > range.max) return `Enter a value from ${range.min} to ${range.max}.`
  return value
}

/** The parsed thresholds, or an error message per invalid field. */
export function validateThresholds(
  form: ThresholdForm,
): { ok: true; values: VitalsThresholds } | { ok: false; errors: ThresholdErrors } {
  const errors: ThresholdErrors = {}
  const values: Partial<Record<ThresholdField, number>> = {}
  for (const spec of THRESHOLD_FIELDS) {
    const result = parseField(form[spec.field], spec)
    if (typeof result === 'string') errors[spec.field] = result
    else values[spec.field] = result
  }

  const { hrMin, hrMax, spo2Min, tempMin, tempMax } = values
  if (hrMin !== undefined && hrMax !== undefined && hrMin >= hrMax) {
    errors.hrMax = 'Must be higher than the lower limit.'
  }
  if (tempMin !== undefined && tempMax !== undefined && tempMin >= tempMax) {
    errors.tempMax = 'Must be higher than the lower limit.'
  }

  if (
    hrMin === undefined ||
    hrMax === undefined ||
    spo2Min === undefined ||
    tempMin === undefined ||
    tempMax === undefined ||
    Object.keys(errors).length > 0
  ) {
    return { ok: false, errors }
  }
  return {
    ok: true,
    values: {
      hr_bpm: { min: hrMin, max: hrMax },
      spo2_pct: { min: spo2Min },
      skin_temp_c: { min: tempMin, max: tempMax },
    },
  }
}
