import type { AlertType } from '../../types/dashboard'

/** Vitals labels match the alert emails (docs/features/vitals-alerts.md). */
export const ALERT_TYPE_LABELS: Record<AlertType, string> = {
  fall: 'Fall detected',
  hr_high: 'High heart rate',
  hr_low: 'Low heart rate',
  spo2_low: 'Low blood oxygen',
  skin_temp_high: 'High skin temperature',
  skin_temp_low: 'Low skin temperature',
  possible_fall: 'Possible fall',
  inactivity: 'Inactivity',
  device_offline: 'Device offline',
  low_battery: 'Low battery',
}

/** Display filter for the "Wearables low on battery" tile. Not an alert threshold. */
export const LOW_BATTERY_DISPLAY_PCT = 20

export function formatHr(value: number | null): string {
  return value === null ? 'No reading' : `${value} bpm`
}

export function formatSpo2(value: number | null): string {
  return value === null ? 'No reading' : `${value}%`
}

export function formatTemp(value: number | null): string {
  return value === null ? 'No reading' : `${value.toFixed(1)} °C`
}

export function formatActivity(value: number | null): string {
  return value === null ? 'No reading' : `${value}%`
}
