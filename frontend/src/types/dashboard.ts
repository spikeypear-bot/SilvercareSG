/**
 * Main-page data shapes. Field names and units follow docs/metrics.md and
 * docs/data-model.md so the demo module can later be replaced by src/lib/api.ts.
 */

/**
 * Resident status as provided by the backend (never computed by the UI), by the rule in
 * docs/data-model.md (Resident status): `ok` no issues; `attention` a non-critical
 * warning or an abnormal reading; `urgent` critical, life-threatening.
 */
export type ResidentStatus = 'ok' | 'attention' | 'urgent'

/**
 * Alert severity as provided by the backend (never computed by the UI): `critical` for
 * falls, the device's `vitals_alert`, and possible falls, `warning` for the cloud vitals
 * check (docs/data-model.md, `silvercare-alerts`).
 */
export type AlertSeverity = 'warning' | 'critical'

/**
 * The backend's alert types, with the same names (lambdas/alert-processor/src/message.py,
 * docs/data-model.md `silvercare-alerts`).
 */
export type AlertType =
  // Health alerts: from the devices and the cloud vitals check (docs/features/vitals-alerts.md),
  // and the watchdog's possible fall at night (docs/features/possible-fall.md).
  | 'fall'
  | 'hr_high'
  | 'hr_low'
  | 'spo2_low'
  | 'skin_temp_high'
  | 'skin_temp_low'
  // Raised by the watchdog, always critical, caregivers and nurses at once
  // (docs/features/possible-fall.md).
  | 'possible_fall'
  // Planned watchdog alerts (docs/metrics.md, "How the backend uses these"): shown in the
  // demo, not raised by the backend yet.
  | 'inactivity'
  // Device problems, raised by the watchdog (docs/features/watchdog.md). Always warnings.
  | 'device_offline'
  | 'battery_low'
  | 'not_worn'

/** One wearable `vitals` message (subset of fields shown in the UI). */
export interface VitalsReading {
  /** ISO 8601 UTC */
  ts: string
  hr_bpm: number | null
  spo2_pct: number | null
  skin_temp_c: number | null
  activity_pct: number | null
}

/**
 * Latest wearable `status` message plus derived online state. `ts`, `battery_pct`, and
 * `worn` are null when the device has never reported (or sent an invalid value).
 */
export interface WearableState {
  device_id: string
  /** ISO 8601 UTC of the latest status message; null if it has never reported. */
  ts: string | null
  battery_pct: number | null
  worn: boolean | null
  charging: boolean
  /** Provided by the backend (never computed by the UI). */
  online: boolean
}

export interface BedroomSensorState {
  device_id: string
  /** ISO 8601 UTC of the latest status message; null if it has never reported. */
  ts: string | null
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
  /** null if the resident has no wearable assigned. */
  wearable: WearableState | null
  /** null if the resident has no bedroom unit assigned. */
  bedroom: BedroomSensorState | null
  /**
   * Latest vitals messages, newest first. `GET /dashboard` gives at most one (the
   * newest, pattern 5); the demo data and the resident page give up to 5.
   */
  vitals: VitalsReading[]
}

/**
 * Alert lifecycle (docs/data-model.md): `acknowledged` is by the care centre nurse for a
 * health alert, by a caregiver (or tech staff, later) for a device problem;
 * `cancelled` is the wearable "I'm OK" cancel (docs/features/alert-cancel.md) or a
 * caregiver's (docs/features/alert-routing.md), from `open` or `acknowledged`. Anything
 * but `open` stops escalation. Only `open` and `acknowledged` alerts are on the open
 * list (pattern 9); a cancel, like a resolve, takes the alert off it.
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
   * ISO 8601 UTC: when the alert was acknowledged. Health alerts are acknowledged by the
   * care centre nurse, with no acknowledger name (the nurse account is shared, so the
   * portal shows "the care centre nurse"; docs/features/alert-routing.md). Device
   * problems are acknowledged by a caregiver (see `acknowledged_by_role`). Kept when the
   * alert is later cancelled or resolved.
   */
  acknowledged_at?: string
  /**
   * Device problems only: who acknowledged, a caregiver or tech staff (`ops`; dormant until
   * that Cognito group exists). Absent for a nurse's acknowledge of a health alert.
   */
  acknowledged_by_role?: 'caregiver' | 'ops'
  /**
   * The acknowledging caregiver's display name (device problems only), looked up on
   * read like `cancelled_by` ("Former caregiver" once the account is deleted). Absent for
   * ops, shown as "the ops team".
   */
  acknowledged_by?: string
  /**
   * The nurse's optional note at acknowledge, 1-500 characters, newlines allowed. Shown
   * as plain text to everyone who can see the alert (docs/data-model.md, `ack_note`).
   */
  ack_note?: string
  /**
   * Staff display name, never a Cognito sub: the API looks it up on read, or returns
   * "Former staff member" once the account is deleted (docs/data-model.md, Actor names).
   * Absent when the watchdog resolved it (device problems, a possible fall back in bed),
   * and on a resolve made on this page before the next load.
   */
  resolved_by?: string
  /** ISO 8601 UTC */
  resolved_at?: string
  /**
   * ISO 8601 UTC: when the escalation check runs, about 300 s after the first
   * notification. Computed by the API (not stored), only on an `open` warning from an
   * assigned device with no `escalated_at` yet.
   */
  escalates_at?: string
  /**
   * ISO 8601 UTC: when the care centre nurse was emailed: at once for a critical alert
   * or one from an unassigned device, or for a warning still open after 5 minutes.
   */
  escalated_at?: string
  /** ISO 8601 UTC: when the cancel was received. Cancelled only. */
  cancelled_at?: string
  /** Who cancelled: the resident (the wearable's "I'm OK") or a caregiver (the portal). */
  cancelled_by_role?: 'resident' | 'caregiver'
  /**
   * The cancelling caregiver's display name, looked up on read like `resolved_by`
   * ("Former caregiver" once the account is deleted). Absent when the resident cancelled.
   */
  cancelled_by?: string
}

export interface DashboardData {
  residents: Resident[]
  /** Newest first */
  alerts: ResidentAlert[]
}

/**
 * `GET /dashboard` response (staff only), exactly as the API returns it
 * (lambdas/api-dashboard/). Mapped to `DashboardData` by `loadDashboardData`.
 * Absent values are null, never missing keys.
 */
export interface ApiDashboardResident {
  resident_id: string
  display_name: string
  home_id: string | null
  status: ResidentStatus
  status_text: string
  wearable: WearableState | null
  bedroom: BedroomSensorState | null
  /** At most one entry: the newest. No `activity_pct`: the staff dashboards don't show it. */
  vitals: Array<Omit<VitalsReading, 'activity_pct'>>
}

export interface ApiDashboardAlert {
  alert_id: string
  resident_id: string
  device_id: string
  alert_type: AlertType
  severity: AlertSeverity
  /** The open list holds only open and acknowledged alerts. */
  status: 'open' | 'acknowledged'
  event_ts: string
  acknowledged_at: string | null
  acknowledged_by_role: 'caregiver' | 'ops' | null
  /** A caregiver's display name (device problems), or "Former caregiver". */
  acknowledged_by: string | null
  ack_note: string | null
  escalated_at: string | null
  escalates_at: string | null
}

export interface ApiDashboardResponse {
  residents: ApiDashboardResident[]
  /** Newest first */
  alerts: ApiDashboardAlert[]
}
