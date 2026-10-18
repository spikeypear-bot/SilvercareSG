import type { AlertType } from '../../types/dashboard'

export const ALERT_TYPE_LABELS: Record<AlertType, string> = {
  fall: 'Fall detected',
  possible_fall: 'Possible fall',
  inactivity: 'Inactivity',
  vitals: 'Vitals need attention',
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
