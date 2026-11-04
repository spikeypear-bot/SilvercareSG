/**
 * Main-page data shapes. Field names and units follow docs/metrics.md and
 * docs/data-model.md so the demo module can later be replaced by src/lib/api.ts.
 */

/** Resident status as provided by the backend (never computed by the UI). */
export type ResidentStatus = 'ok' | 'attention' | 'urgent'

/**
 * Alert severity as provided by the backend (never computed by the UI): `critical` for
 * falls and the device's `vitals_alert`, `warning` for the cloud vitals check
 * (docs/data-model.md, `silvercare-alerts`).
 */
export type AlertSeverity = 'warning' | 'critical'

export type AlertType =
  // Raised by the backend today (docs/features/vitals-alerts.md).
  | 'fall'
  | 'hr_high'
  | 'hr_low'
  | 'spo2_low'
  | 'skin_temp_high'
  | 'skin_temp_low'
  // Planned watchdog alerts (docs/metrics.md, "How the backend uses these"): shown in the
  // demo, not raised by the backend yet.
  | 'possible_fall'
  | 'inactivity'
  | 'device_offline'
  | 'low_battery'

/** One wearable `vitals` message (subset of fields shown in the UI). */
export interface VitalsReading {
  /** ISO 8601 UTC */
  ts: string
  hr_bpm: number | null
  spo2_pct: number | null
  skin_temp_c: number | null
  activity_pct: number
}

/** Latest wearable `status` message plus derived online state. */
export interface WearableState {
  device_id: string
  /** ISO 8601 UTC of the latest status message */
  ts: string
  battery_pct: number
  worn: boolean
  charging: boolean
  online: boolean
}

export interface BedroomSensorState {
  device_id: string
  /** ISO 8601 UTC of the latest status message */
  ts: string
  online: boolean
}

export interface Resident {
  resident_id: string
  name: string
  /** Opaque ID of the home the devices are in (docs/data-model.md). No address is stored. */
  home_id: string
  status: ResidentStatus
  /** Plain sentence describing the status, provided with the data. */
  status_text: string
  wearable: WearableState
  bedroom: BedroomSensorState
  /** Latest 5 vitals messages, newest first. */
  vitals: VitalsReading[]
}

/**
 * Alert lifecycle (docs/data-model.md): `cancelled` is the wearable "I'm OK" cancel
 * (docs/features/alert-cancel.md). Anything but `open` stops escalation. Only `open` and
 * `acknowledged` alerts are on the open list (pattern 9); a cancel, like a resolve, takes
 * the alert off it.
 */
export type AlertStatus = 'open' | 'acknowledged' | 'cancelled' | 'resolved'

export interface ResidentAlert {
  alert_id: string
  resident_id: string
  device_id: string
  alert_type: AlertType
  severity: AlertSeverity
  status: AlertStatus
  /** ISO 8601 UTC of the triggering event */
  event_ts: string
  /**
   * Display name of the staff member or linked caregiver, never a Cognito sub: the API
   * looks it up on read, or returns "Former staff member" / "Former caregiver" once the
   * account is deleted (docs/data-model.md, Actor names).
   */
  acknowledged_by?: string
  /** ISO 8601 UTC */
  acknowledged_at?: string
  /** Staff display name, looked up on read like `acknowledged_by`. */
  resolved_by?: string
  /** ISO 8601 UTC */
  resolved_at?: string
  /**
   * ISO 8601 UTC: when the escalation check runs, about 60 s after the first
   * notification. Computed by the API (not stored), only on an `open` alert with no
   * `escalated_at` yet.
   */
  escalates_at?: string
  /** ISO 8601 UTC: when nurses were emailed because nobody responded within 60 s. */
  escalated_at?: string
  /** ISO 8601 UTC: when the cloud received the resident's "I'm OK" press. Cancelled only. */
  cancelled_at?: string
  /** Who cancelled; `resident` (the wearable button) is the only value today. */
  cancelled_by_role?: 'resident'
}

export interface DashboardData {
  residents: Resident[]
  /** Newest first */
  alerts: ResidentAlert[]
}
