import type { AlertLimits, AlertLimitsStatus, DeviceLimits } from '../../types/devices'

/** The loosest limits a wearable may use (docs/metrics.md, `vitals_alert`). */
export const FIXED_DEVICE_LIMITS: DeviceLimits = { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 88 } }

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const own = Object.keys(value)
  return own.length === keys.length && keys.every((k) => own.includes(k))
}

function intIn(value: unknown, min: number, max: number): value is number {
  return Number.isInteger(value) && (value as number) >= min && (value as number) <= max
}

/** Valid limits (spec, Valid limits), or null. Copies the numbers, never the input object. */
export function parseDeviceLimits(value: unknown): DeviceLimits | null {
  if (!isPlainObject(value) || !hasOnlyKeys(value, ['hr_bpm', 'spo2_pct'])) return null
  const hr = value.hr_bpm
  const spo2 = value.spo2_pct
  if (!isPlainObject(hr) || !hasOnlyKeys(hr, ['min', 'max'])) return null
  if (!isPlainObject(spo2) || !hasOnlyKeys(spo2, ['min'])) return null
  if (!intIn(hr.min, 45, 250) || !intIn(hr.max, 20, 130) || hr.min >= hr.max) return null
  if (!intIn(spo2.min, 88, 100)) return null
  return { hr_bpm: { min: hr.min, max: hr.max }, spo2_pct: { min: spo2.min } }
}

const STATUSES: readonly AlertLimitsStatus[] = ['in_sync', 'pending', 'unknown']

/**
 * A device's `alert_limits` from `GET /devices`, or null if it isn't an object. An
 * unrecognised status reads as `unknown`; invalid limits become null; `desired` and
 * `reported` are kept only when the API sent them (staff), so ops never get them.
 */
export function parseAlertLimits(value: unknown): AlertLimits | null {
  if (!isPlainObject(value)) return null
  const status = STATUSES.find((s) => s === value.status) ?? 'unknown'
  return {
    status,
    ...('desired' in value && { desired: parseDeviceLimits(value.desired) }),
    ...('reported' in value && { reported: parseDeviceLimits(value.reported) }),
  }
}

/** "HR 45-130 bpm · SpO₂ ≥ 88 %" */
export function formatDeviceLimits(limits: DeviceLimits): string {
  return `HR ${limits.hr_bpm.min}-${limits.hr_bpm.max} bpm · SpO₂ ≥ ${limits.spo2_pct.min} %`
}

export const ALERT_LIMITS_STATUS_LABELS: Record<AlertLimitsStatus, string> = {
  in_sync: 'Limits up to date',
  pending: 'Waiting for device',
  unknown: 'Status unknown',
}

/**
 * A wearable's limits from its resident's (already resolved) thresholds: the stricter of
 * each threshold and 45 / 130 / 88; heart rate falls back to 45 / 130 if min >= max.
 */
export function wearableLimits(thresholds: { hr_bpm: { min: number; max: number }; spo2_pct: { min: number } }): DeviceLimits {
  const fixed = FIXED_DEVICE_LIMITS
  const min = Math.max(thresholds.hr_bpm.min, fixed.hr_bpm.min)
  const max = Math.min(thresholds.hr_bpm.max, fixed.hr_bpm.max)
  return {
    hr_bpm: min < max ? { min, max } : { ...fixed.hr_bpm },
    spo2_pct: { min: Math.max(thresholds.spo2_pct.min, fixed.spo2_pct.min) },
  }
}

/** "The wearable sends a critical alert below 45 or above 130 bpm, or SpO₂ below 88 %." */
export function wearableLimitsSentence(limits: DeviceLimits): string {
  return `The wearable sends a critical alert below ${limits.hr_bpm.min} or above ${limits.hr_bpm.max} bpm, or SpO₂ below ${limits.spo2_pct.min} %.`
}
