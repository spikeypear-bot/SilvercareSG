/**
 * Resident detail page shapes (docs/data-model.md patterns 10, 11, 12, 18). Field names
 * and units follow docs/metrics.md and the `silvercare-latest` allow-list, so the demo
 * module can later be replaced by src/lib/api.ts.
 */
import type {
  AlertStatus,
  ApiDashboardAlert,
  ApiDashboardResident,
  Resident,
  ResidentAlert,
  VitalsReading,
} from './dashboard'

/** Fields every stored entry keeps besides its message fields. */
interface LatestEntry {
  message_id: string
  /** ISO 8601 UTC */
  ts: string
}

export type VitalsEntry = LatestEntry & Omit<VitalsReading, 'ts'>

export interface FallEntry extends LatestEntry {
  /** 0.0–1.0 */
  confidence: number
  peak_g: number
  still_s: number
}

export interface WearableStatusEntry extends LatestEntry {
  battery_pct: number
  worn: boolean
  charging: boolean
  fw_version: string
}

export interface MotionEntry extends LatestEntry {
  motion: boolean
}

export interface BedEntry extends LatestEntry {
  occupied: boolean
}

export interface BedroomStatusEntry extends LatestEntry {
  /** null if mains-powered */
  battery_pct: number | null
  fw_version: string
}

/** Latest 5 entries per message type, newest first (pattern 11). */
export interface WearableLatest {
  vitals: VitalsEntry[]
  fall: FallEntry[]
  status: WearableStatusEntry[]
}

export interface BedroomLatest {
  motion: MotionEntry[]
  bed: BedEntry[]
  status: BedroomStatusEntry[]
}

/** One device assigned to the resident (pattern 10) with its latest messages. */
export interface WearableDevice {
  device_id: string
  device_type: 'wearable'
  /** Provided by the backend from the latest `status` time (never computed by the UI). */
  online: boolean
  latest: WearableLatest
}

export interface BedroomDevice {
  device_id: string
  device_type: 'bedroom'
  online: boolean
  latest: BedroomLatest
}

export interface ThresholdRange {
  min: number
  max: number
}

/** Per-resident vitals limits (docs/data-model.md, Resident thresholds). */
export interface VitalsThresholds {
  hr_bpm: ThresholdRange
  spo2_pct: { min: number }
  skin_temp_c: ThresholdRange
}

/**
 * Limits as the API resolves them (each the resident's own value or the default,
 * docs/data-model.md). `updated_*` are absent until someone changes them.
 */
export interface ResidentThresholds extends VitalsThresholds {
  /** ISO 8601 UTC */
  updated_at?: string
  /** Staff display name, never a Cognito sub (stored at write time). */
  updated_by?: string
}

/** One threshold value, as named in the change history (docs/data-model.md, Threshold change history). */
export type ThresholdKey = 'hr_bpm.min' | 'hr_bpm.max' | 'spo2_pct.min' | 'skin_temp_c.min' | 'skin_temp_c.max'

export interface ThresholdValueChange {
  field: ThresholdKey
  old_value: number
  new_value: number
}

/** One saved change to the thresholds. Written by the server only, never by the UI. */
export interface ThresholdChangeEntry {
  change_id: string
  /** ISO 8601 UTC */
  changed_at: string
  /** Staff display name, never a Cognito sub (the API maps the sub to a name). */
  changed_by: string
  /** Only the values that changed. */
  changes: ThresholdValueChange[]
}

export interface ResidentDetail {
  /** Profile and current state, as on the dashboards. */
  resident: Resident
  /** null when no device of that type is assigned. */
  wearable: WearableDevice | null
  bedroom: BedroomDevice | null
  /** Every alert still stored for this resident, newest first, including resolved ones. */
  alerts: ResidentAlert[]
  thresholds: ResidentThresholds
  /** Earlier threshold changes, newest first (Threshold change history). */
  threshold_history: ThresholdChangeEntry[]
}

/**
 * `GET /residents/{resident_id}` and `GET /me/residents` (lambdas/api-residents/), exactly
 * as the API returns them; absent values are null. Mapped by `toResidentDetail` and the
 * caregiver loader in useDashboardData.ts.
 */
export interface ApiResidentAlert extends Omit<ApiDashboardAlert, 'status'> {
  status: AlertStatus
  resolved_at: string | null
  /** Staff display name, or "Former staff member". Null for a watchdog resolve. */
  resolved_by: string | null
  cancelled_at: string | null
  cancelled_by_role: 'resident' | 'caregiver' | null
  /** Caregiver display name, or "Former caregiver". */
  cancelled_by: string | null
}

/** The dashboard's resident shape, with up to 5 vitals including `activity_pct`. */
export interface ApiLinkedResident extends Omit<ApiDashboardResident, 'vitals'> {
  vitals: VitalsReading[]
}

export interface ApiResidentDetail {
  resident: ApiLinkedResident
  wearable: WearableDevice | null
  bedroom: BedroomDevice | null
  /** Every stored alert, newest first. */
  alerts: ApiResidentAlert[]
  /** Resolved limits; `updated_*` only for staff, and only once changed. */
  thresholds: VitalsThresholds & { updated_at: string | null; updated_by: string | null }
  /** Always []: the history comes from `GET .../thresholds/history` (ApiThresholdChange). */
  threshold_history: ThresholdChangeEntry[]
}

/** `GET /residents/{resident_id}/thresholds/history`: one change, the stored name only (never a sub). */
export interface ApiThresholdChange {
  change_id: string
  changed_at: string
  changed_by_name: string
  changes: ThresholdValueChange[]
}

/** `PUT /residents/{resident_id}/thresholds`: the thresholds now stored. */
export interface ApiThresholdsSaveResponse {
  /** false when the save matched the current values: nothing was written. */
  changed: boolean
  thresholds: ApiResidentDetail['thresholds']
}

export interface ApiMyResidentsResponse {
  residents: ApiLinkedResident[]
  /** Every stored alert of those residents, newest first. */
  alerts: ApiResidentAlert[]
}
