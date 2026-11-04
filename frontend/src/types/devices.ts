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
}
