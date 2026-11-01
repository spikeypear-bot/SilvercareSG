/**
 * Main-page data shapes. Field names and units follow docs/metrics.md and
 * docs/data-model.md so the demo module can later be replaced by src/lib/api.ts.
 */

/** Resident status as provided by the backend (never computed by the UI). */
export type ResidentStatus = 'ok' | 'attention' | 'urgent'

/** Alert severity as provided by the backend (never computed by the UI). */
export type AlertSeverity = 'info' | 'warning' | 'critical'

export type AlertType =
  | 'fall'
  | 'possible_fall'
  | 'inactivity'
  | 'vitals'
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

/** Alert lifecycle (docs/data-model.md pattern 16). */
export type AlertStatus = 'open' | 'acknowledged' | 'resolved'

export interface ResidentAlert {
  alert_id: string
  resident_id: string
  device_id: string
  alert_type: AlertType
  severity: AlertSeverity
  status: AlertStatus
  /** ISO 8601 UTC of the triggering event */
  event_ts: string
  /** Staff display name, never a Cognito sub (the API maps the sub to a name). */
  acknowledged_by?: string
  /** ISO 8601 UTC */
  acknowledged_at?: string
  /** Staff display name, never a Cognito sub. */
  resolved_by?: string
  /** ISO 8601 UTC */
  resolved_at?: string
  /** ISO 8601 UTC: when an alert still in its confirmation window escalates to nurses. */
  escalates_at?: string
}

export interface DashboardData {
  residents: Resident[]
  /** Newest first */
  alerts: ResidentAlert[]
}
