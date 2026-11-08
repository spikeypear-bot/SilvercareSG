/**
 * `GET /ops/device-problems` (ops only; docs/data-model.md, the ops paragraph of Access
 * patterns). The server filters to device problems; this type only describes them, it
 * is not the access control.
 */
export type DeviceProblemType = 'device_offline' | 'battery_low' | 'not_worn'

/** `details` keeps only the documented keys of each type. */
export interface DeviceProblemDetails {
  last_seen?: string
  grouped?: boolean
  battery_pct?: number
  since?: string
}

export interface DeviceProblem {
  /** `res-NNNN`, or `unassigned` for a device not assigned to anyone. */
  resident_id: string
  sk: string
  alert_id: string
  alert_type: DeviceProblemType
  severity: 'warning' | 'critical' | null
  device_id: string | null
  event_ts: string
  status: 'open' | 'acknowledged' | 'resolved' | 'cancelled' | null
  details: DeviceProblemDetails | null
  acknowledged_at: string | null
  acknowledged_by_role: 'ops' | 'caregiver' | null
  /** "the ops team", the caregiver's name, or null (lookup failed / not acknowledged). */
  acknowledged_by: string | null
  resolved_at: string | null
  resolved_by_role: 'system' | null
}

export interface DeviceProblemsResponse {
  alerts: DeviceProblem[]
}
