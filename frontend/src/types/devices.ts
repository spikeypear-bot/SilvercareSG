/**
 * Devices view shapes: every device (docs/data-model.md pattern 4), its newest `status`
 * (pattern 5), and the resident it is assigned to (pattern 8). Field names follow
 * docs/metrics.md.
 */

export type DeviceType = 'wearable' | 'bedroom'

/** Opaque IDs and the display name only; no other profile data. */
export interface DeviceAssignment {
  resident_id: string
  name: string
  home_id: string
}

export interface FleetDevice {
  device_id: string
  device_type: DeviceType
  /** null for a spare that isn't assigned to anyone. */
  assigned_to: DeviceAssignment | null
  /** Provided by the backend from the latest `status` time (never computed by the UI). */
  online: boolean
  /** ISO 8601 UTC of the latest `status` message; null if the device has never reported. */
  last_seen: string | null
  /** null if mains-powered (bedroom sensors only) or never reported. */
  battery_pct: number | null
  /** Wearables only; always false for bedroom sensors. */
  charging: boolean
  /** null if the device has never reported. */
  fw_version: string | null
  /**
   * The wearable's on-device alert limits (docs/features/device-alert-limits.md), already
   * checked by `toFleetDevice`. null for bedroom sensors; absent or null for a wearable
   * means the API sent nothing usable, shown as "Status unknown".
   */
  alert_limits?: AlertLimits | null
}

/**
 * `GET /devices` response (staff only), exactly as the API returns it. Mapped to
 * `FleetDevice` by `loadDeviceFleet`.
 */
export interface ApiDeviceAssignment {
  resident_id: string
  /** null only if the resident has no valid profile. */
  display_name: string | null
  /** null if the device item has no valid home_id. */
  home_id: string | null
}

export interface ApiDevice {
  device_id: string
  device_type: DeviceType
  online: boolean
  last_seen: string | null
  battery_pct: number | null
  charging: boolean
  fw_version: string | null
  assigned_to: ApiDeviceAssignment | null
  /** null for bedroom sensors. Optional: parsed defensively by `toFleetDevice`. */
  alert_limits?: AlertLimits | null
}

export interface DevicesResponse {
  devices: ApiDevice[]
}

/** A wearable's on-device `vitals_alert` limits (docs/features/device-alert-limits.md, Device limits). */
export interface DeviceLimits {
  hr_bpm: { min: number; max: number }
  spo2_pct: { min: number }
}

export type AlertLimitsStatus = 'in_sync' | 'pending' | 'unknown'

/**
 * `alert_limits` of a wearable in `GET /devices`. Staff get `desired` and `reported`
 * (null when missing or invalid); ops get `status` only.
 */
export interface AlertLimits {
  status: AlertLimitsStatus
  desired?: DeviceLimits | null
  reported?: DeviceLimits | null
}
