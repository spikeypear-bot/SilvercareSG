/**
 * Resident detail page shapes (docs/data-model.md patterns 10, 11, 12, 18). Field names
 * and units follow docs/metrics.md and the `silvercare-latest` allow-list, so the demo
 * module can later be replaced by src/lib/api.ts.
 */
import type { Resident, ResidentAlert, VitalsReading } from './dashboard'

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

export interface ResidentThresholds extends VitalsThresholds {
  /** ISO 8601 UTC */
  updated_at: string
  /** Staff display name, never a Cognito sub (the API maps the sub to a name). */
  updated_by: string
}

/** One threshold value, as named in the change history (docs/data-model.md pattern 22). */
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
  /** Earlier threshold changes, newest first (pattern 22). */
  threshold_history: ThresholdChangeEntry[]
}
