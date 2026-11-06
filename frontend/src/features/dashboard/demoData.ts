/**
 * Synthetic demo data for the main page and the resident page. No real resident information.
 *
 * Shapes and units follow docs/metrics.md (`vitals`, `status`) and the alerts table in
 * docs/data-model.md. `status`, `status_text`, and alert `severity` are provided here as
 * the API will later provide them; the UI never derives them from vitals.
 */
import type {
  AlertSeverity,
  AlertType,
  DashboardData,
  Resident,
  ResidentAlert,
  ResidentStatus,
} from '../../types/dashboard'
import type {
  BedroomDevice,
  ResidentDetail,
  ResidentThresholds,
  ThresholdChangeEntry,
  ThresholdKey,
  ThresholdValueChange,
  VitalsThresholds,
  WearableDevice,
} from '../../types/resident'
import type { FleetDevice } from '../../types/devices'
import { isOnOpenList } from './stats'

/** The demo residents linked to every caregiver account (the caregiver view). */
export const FAMILY_LINKED_RESIDENT_IDS: readonly string[] = ['res-0002', 'res-0005']

export const STATUS_TEXT: Record<ResidentStatus, string> = {
  ok: 'All readings look normal',
  attention: 'Needs attention',
  urgent: 'Urgent: check now',
}

interface ResidentSeed {
  status: ResidentStatus
  hr: number
  spo2: number
  temp: number
  activity: number
  battery: number
  worn: boolean
  charging: boolean
  /** Minutes since the wearable's last `status` (sent every 5 min while it is online). */
  wearableLastSeenMin: number
  bedroomLastSeenMin: number
  /** Bedroom sensor battery; `null` = mains-powered. Defaults from the resident number. */
  bedroomBattery?: number | null
  /** Minutes since the newest vitals reading. */
  vitalsAgeMin: number
}

/**
 * A device is offline after 15 minutes with no `status` message (docs/metrics.md, still
 * proposed). The backend applies this rule and sends `online`; the demo applies it here
 * so the UI never computes it.
 */
export const DEMO_OFFLINE_TIMEOUT_MIN = 15

const ok = (overrides: Partial<ResidentSeed> = {}): ResidentSeed => ({
  status: 'ok',
  hr: 72,
  spo2: 97,
  temp: 33.6,
  activity: 18,
  battery: 80,
  worn: true,
  charging: false,
  wearableLastSeenMin: 2,
  bedroomLastSeenMin: 3,
  vitalsAgeMin: 1,
  ...overrides,
})

// One entry per resident, in resident-ID order (Test Resident A = res-0001, ...).
const SEEDS: readonly ResidentSeed[] = [
  ok({ hr: 68, battery: 82, activity: 22 }), // A
  ok({ status: 'attention', hr: 64, activity: 0, battery: 64, temp: 33.1 }), // B (family)
  ok({ status: 'urgent', hr: 96, spo2: 95, activity: 3, battery: 71 }), // C
  ok({ hr: 75, spo2: 98, battery: 45, activity: 30, bedroomBattery: 9 }), // D: bedroom sensor low
  ok({ hr: 70, spo2: 97, temp: 34.0, battery: 91, activity: 26 }), // E (family)
  ok({ status: 'attention', hr: 78, battery: 15, activity: 12, bedroomLastSeenMin: 26 * 60 }), // F
  ok({ status: 'urgent', hr: 128, spo2: 94, temp: 34.6, battery: 77, activity: 5 }), // G
  ok({ hr: 66, spo2: 91, battery: 77, activity: 9 }), // H: SpO2 just under the default 92
  // I: bedroom sensor offline, wearable fine.
  ok({ status: 'attention', hr: 74, battery: 69, bedroomLastSeenMin: 70 }), // I
  ok({ hr: 71, battery: 58, worn: false, charging: true, vitalsAgeMin: 35 }), // J
  ok({ hr: 80, spo2: 96, battery: 88, activity: 41 }), // K
  // L: wearable offline just past the 15 min timeout.
  ok({ status: 'attention', hr: 88, battery: 54, activity: 2, wearableLastSeenMin: 18, vitalsAgeMin: 18 }), // L
  ok({ hr: 69, battery: 12, activity: 15 }), // M
  ok({ hr: 73, spo2: 99, battery: 69, activity: 20 }), // N
  // O: both devices offline.
  ok({
    status: 'attention',
    hr: 76,
    battery: 38,
    wearableLastSeenMin: 52,
    vitalsAgeMin: 52,
    bedroomLastSeenMin: 185,
  }), // O
  ok({ hr: 62, battery: 95, temp: 33.0, activity: 7 }), // P
  ok({ status: 'urgent', hr: 102, spo2: 95, battery: 60, activity: 1, bedroomLastSeenMin: 2 * 24 * 60 }), // Q
  ok({ hr: 77, battery: 73, activity: 33 }), // R
  ok({ status: 'attention', hr: 82, spo2: 91, battery: 66, activity: 10 }), // S
  ok({ hr: 70, battery: 27, activity: 24 }), // T
]

/**
 * A warning still `open` this long after its first notification escalates to the care
 * centre nurse (docs/features/escalation.md, alert-routing.md: 5 minutes). Critical
 * alerts are sent to the nurse at once instead. The demo treats the notification as
 * sent at `event_ts`.
 */
export const DEMO_ESCALATION_DELAY_MS = 300_000

/** The demo's nurse email for a critical alert goes out this soon after `event_ts`. */
const DEMO_AT_ONCE_MS = 1_000

interface AlertSeed {
  resident: number // 1-based resident number
  type: AlertType
  severity: AlertSeverity
  device: 'wearable' | 'bedroom'
  minutesAgo: number
  /** Acknowledged by the care centre nurse this many minutes ago, with an optional synthetic note. */
  acknowledged?: { minutesAgo: number; note?: string }
}

const ALERT_SEEDS: readonly AlertSeed[] = [
  // Mid-countdown on every page load: 2:30 into the 5-minute escalation delay. A cloud
  // vitals check warning: possible falls are critical, so they never count down
  // (docs/features/possible-fall.md).
  { resident: 8, type: 'spo2_low', severity: 'warning', device: 'wearable', minutesAgo: 2.5 },
  { resident: 3, type: 'fall', severity: 'critical', device: 'wearable', minutesAgo: 4 },
  {
    resident: 7,
    type: 'hr_high',
    severity: 'critical',
    device: 'wearable',
    minutesAgo: 9,
    acknowledged: { minutesAgo: 6, note: 'Synthetic note: ambulance on the way, 8 minutes.' },
  },
  { resident: 12, type: 'possible_fall', severity: 'critical', device: 'bedroom', minutesAgo: 15 },
  { resident: 17, type: 'fall', severity: 'critical', device: 'wearable', minutesAgo: 22 },
  { resident: 19, type: 'spo2_low', severity: 'warning', device: 'wearable', minutesAgo: 27 },
  { resident: 2, type: 'inactivity', severity: 'warning', device: 'bedroom', minutesAgo: 38 },
  {
    resident: 15,
    type: 'device_offline',
    severity: 'warning',
    device: 'wearable',
    minutesAgo: 48,
    acknowledged: { minutesAgo: 40, note: 'Synthetic note: called the caregiver.\nWearable to be checked at 3 pm.' },
  },
  { resident: 6, type: 'low_battery', severity: 'warning', device: 'wearable', minutesAgo: 55 },
  { resident: 9, type: 'device_offline', severity: 'warning', device: 'bedroom', minutesAgo: 70 },
  {
    resident: 13,
    type: 'low_battery',
    severity: 'warning',
    device: 'wearable',
    minutesAgo: 125,
    acknowledged: { minutesAgo: 110 },
  },
  { resident: 2, type: 'possible_fall', severity: 'critical', device: 'bedroom', minutesAgo: 410 },
  // The caregiver view's demo countdown: B is linked to every caregiver, and this warning
  // has 3:00 left on every page load. B's previous inactivity warning (38 min ago) is past
  // the 30-minute repeat hold-back, so this is a new alert. Last, so the IDs above stay
  // stable.
  { resident: 2, type: 'inactivity', severity: 'warning', device: 'bedroom', minutesAgo: 2 },
]

const LETTERS = 'ABCDEFGHIJKLMNOPQRST'
const HR_WOBBLE = [0, 2, -1, 3, -2]
const SPO2_WOBBLE = [0, -1, 0, 1, 0]
const TEMP_WOBBLE = [0, 0.1, -0.1, 0.2, 0]
const ACTIVITY_WOBBLE = [0, 4, -3, 6, 2]

const pad4 = (n: number) => String(n).padStart(4, '0')
const minutesBefore = (now: Date, minutes: number) =>
  new Date(now.getTime() - minutes * 60_000).toISOString()
const clamp = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n))

// Device numbers are opaque (no relationship to resident numbers), as in docs/metrics.md.
const wearableNumber = (i: number) => ((i * 7) % 20) + 1
const bedroomNumber = (i: number) => ((i * 13 + 5) % 20) + 1
const homeNumber = (i: number) => ((i * 11 + 3) % 20) + 1

export const residentId = (n: number) => `res-${pad4(n)}`

/**
 * Escalation fields as the API returns them (docs/features/alert-routing.md): a critical
 * alert has `escalated_at` at once. A warning has `escalated_at` once the 5-minute check
 * found it still open, or the computed `escalates_at` while the check is still to come,
 * and nothing if it was acknowledged within 5 minutes.
 */
function escalation(
  eventTs: string,
  severity: AlertSeverity,
  acknowledgedAt: string | undefined,
  now: Date,
): Pick<ResidentAlert, 'escalates_at' | 'escalated_at'> {
  if (severity === 'critical') {
    return { escalated_at: new Date(Date.parse(eventTs) + DEMO_AT_ONCE_MS).toISOString() }
  }
  const at = new Date(Date.parse(eventTs) + DEMO_ESCALATION_DELAY_MS).toISOString()
  if (acknowledgedAt && acknowledgedAt <= at) return {}
  return Date.parse(at) <= now.getTime() ? { escalated_at: at } : { escalates_at: at }
}

/** Builds the full demo data set with timestamps relative to `now`. */
export function createDemoData(now: Date = new Date()): DashboardData {
  const residents: Resident[] = SEEDS.map((seed, i) => {
    const vitals = HR_WOBBLE.map((_, r) => ({
      ts: minutesBefore(now, seed.vitalsAgeMin + r),
      hr_bpm: seed.hr + HR_WOBBLE[r],
      // Occasional missing SpO2 reading (common during movement).
      spo2_pct: i % 6 === 3 && r === 2 ? null : clamp(seed.spo2 + SPO2_WOBBLE[r], 50, 100),
      skin_temp_c: Math.round((seed.temp + TEMP_WOBBLE[r]) * 10) / 10,
      activity_pct: clamp(seed.activity + ACTIVITY_WOBBLE[r], 0, 100),
    }))

    return {
      resident_id: residentId(i + 1),
      name: `Test Resident ${LETTERS[i]}`,
      home_id: `home-${pad4(homeNumber(i))}`,
      status: seed.status,
      status_text: STATUS_TEXT[seed.status],
      wearable: {
        device_id: `wearable-${pad4(wearableNumber(i))}`,
        ts: minutesBefore(now, seed.wearableLastSeenMin),
        battery_pct: seed.battery,
        worn: seed.worn,
        charging: seed.charging,
        online: seed.wearableLastSeenMin < DEMO_OFFLINE_TIMEOUT_MIN,
      },
      bedroom: {
        device_id: `bedroom-${pad4(bedroomNumber(i))}`,
        ts: minutesBefore(now, seed.bedroomLastSeenMin),
        online: seed.bedroomLastSeenMin < DEMO_OFFLINE_TIMEOUT_MIN,
      },
      vitals,
    }
  })

  const alerts: ResidentAlert[] = ALERT_SEEDS.map((seed, i): ResidentAlert => {
    const resident = residents[seed.resident - 1]
    const eventTs = minutesBefore(now, seed.minutesAgo)
    return {
      alert_id: `6f1c2e0a-5b3d-5e8f-9a47-${String(i + 1).padStart(12, '0')}`,
      resident_id: resident.resident_id,
      device_id: seed.device === 'wearable' ? resident.wearable.device_id : resident.bedroom.device_id,
      alert_type: seed.type,
      severity: seed.severity,
      status: seed.acknowledged ? 'acknowledged' : 'open',
      event_ts: eventTs,
      ...(seed.acknowledged && {
        acknowledged_at: minutesBefore(now, seed.acknowledged.minutesAgo),
        ...(seed.acknowledged.note && { ack_note: seed.acknowledged.note }),
      }),
      ...escalation(
        eventTs,
        seed.severity,
        seed.acknowledged && minutesBefore(now, seed.acknowledged.minutesAgo),
        now,
      ),
    }
  }).sort((a, b) => b.event_ts.localeCompare(a.event_ts))

  return { residents, alerts }
}

// ---------------------------------------------------------------------------
// Resident detail page (patterns 10, 11, 12, 18)

/**
 * The backend's default thresholds (docs/data-model.md, Resident thresholds; user's
 * decision 2026-11-02; synthetic, to be checked with a clinician before any real use).
 */
export const DEMO_DEFAULT_THRESHOLDS: VitalsThresholds = {
  hr_bpm: { min: 50, max: 120 },
  spo2_pct: { min: 92 },
  skin_temp_c: { min: 30.0, max: 38.0 },
}

const DEMO_STAFF = ['Test Nurse A', 'Test Nurse B', 'Test Nurse C'] as const
const DEMO_CAREGIVERS = ['Test Caregiver A', 'Test Caregiver B'] as const
const DEMO_DOCTORS = ['Test Doctor A', 'Test Doctor B'] as const

interface ThresholdChangeSeed {
  daysAgo: number
  by: string
  changes: ThresholdValueChange[]
}

/**
 * Earlier threshold changes, newest first (1-based resident number). Every resident
 * starts from DEMO_DEFAULT_THRESHOLDS, so applying these oldest first gives today's
 * limits: only B (res-0002) has its own limit, as the backend's seed script creates.
 */
function thresholdChangeSeeds(n: number): ThresholdChangeSeed[] {
  if (n === 2) {
    // B: lower normal SpO2 (scripts/seed-registry.py: res-0002 `spo2_pct {"min": 88}`).
    return [{ daysAgo: 9, by: DEMO_DOCTORS[1], changes: [{ field: 'spo2_pct.min', old_value: 92, new_value: 88 }] }]
  }
  const by = DEMO_DOCTORS[n % 2]
  switch (n % 3) {
    // Lowered or widened for a while, then set back, so the limits are the defaults again
    // and every old_value is the value before it.
    case 1:
      return [
        { daysAgo: 4 + n, by, changes: [{ field: 'skin_temp_c.min', old_value: 29.5, new_value: 30.0 }] },
        { daysAgo: 10 + n, by, changes: [{ field: 'skin_temp_c.min', old_value: 30.0, new_value: 29.5 }] },
      ]
    case 2:
      return [
        {
          daysAgo: 3 + n,
          by,
          changes: [
            { field: 'hr_bpm.min', old_value: 45, new_value: 50 },
            { field: 'hr_bpm.max', old_value: 125, new_value: 120 },
          ],
        },
        {
          daysAgo: 6 + n,
          by,
          changes: [
            { field: 'hr_bpm.min', old_value: 50, new_value: 45 },
            { field: 'hr_bpm.max', old_value: 120, new_value: 125 },
          ],
        },
      ]
    default:
      return []
  }
}

function withValue(t: VitalsThresholds, field: ThresholdKey, value: number): VitalsThresholds {
  switch (field) {
    case 'hr_bpm.min':
      return { ...t, hr_bpm: { ...t.hr_bpm, min: value } }
    case 'hr_bpm.max':
      return { ...t, hr_bpm: { ...t.hr_bpm, max: value } }
    case 'spo2_pct.min':
      return { ...t, spo2_pct: { min: value } }
    case 'skin_temp_c.min':
      return { ...t, skin_temp_c: { ...t.skin_temp_c, min: value } }
    case 'skin_temp_c.max':
      return { ...t, skin_temp_c: { ...t.skin_temp_c, max: value } }
  }
}

const HISTORY_TYPES: ReadonlyArray<{ type: AlertType; severity: AlertSeverity; device: 'wearable' | 'bedroom' }> = [
  { type: 'low_battery', severity: 'warning', device: 'wearable' },
  { type: 'hr_high', severity: 'warning', device: 'wearable' },
  { type: 'possible_fall', severity: 'critical', device: 'bedroom' },
  { type: 'device_offline', severity: 'warning', device: 'bedroom' },
  { type: 'fall', severity: 'critical', device: 'wearable' },
  { type: 'inactivity', severity: 'warning', device: 'bedroom' },
]

/** Resident with no alerts at all, so the empty alert history shows (Test Resident N). */
const NO_HISTORY_RESIDENT = 14

/** Synthetic UUID v4-shaped ID. Deterministic so the same page load shows the same data. */
const demoUuid = (group: string, n: number) =>
  `0d3e0000-0000-4000-8${group.padStart(3, '0')}-${String(n).padStart(12, '0')}`

/**
 * Closed alerts from the last 30 days: resolved or cancelled ones expire 30 days later
 * (docs/data-model.md Retention). Some were acknowledged first by the care centre nurse,
 * a few with a note. A few wearable alerts were cancelled by the resident's "I'm OK"
 * press (docs/features/alert-cancel.md), others by a caregiver in the portal, some after
 * the acknowledge (docs/features/alert-routing.md). A few were resolved or cancelled by
 * someone whose account has since been deleted, so the API shows "Former staff member" /
 * "Former caregiver" (Actor names).
 */
function historyAlerts(resident: Resident, i: number, now: Date): ResidentAlert[] {
  if (i + 1 === NO_HISTORY_RESIDENT) return []
  const count = 1 + (i % 4)
  return Array.from({ length: count }, (_, k): ResidentAlert => {
    const kind = HISTORY_TYPES[(i + k * 2) % HISTORY_TYPES.length]
    const minutesAgo = ((k + 1) * (3 + (i % 5)) * 24 - (i % 7)) * 60
    const staff = DEMO_STAFF[(i + k) % DEMO_STAFF.length]
    const acknowledgedFirst = (i + k) % 3 !== 0
    const base = {
      alert_id: `6f1c2e0a-5b3d-5e8f-9a47-1${String(i + 1).padStart(3, '0')}${String(k + 1).padStart(8, '0')}`,
      resident_id: resident.resident_id,
      device_id: kind.device === 'wearable' ? resident.wearable.device_id : resident.bedroom.device_id,
      alert_type: kind.type,
      severity: kind.severity,
      event_ts: minutesBefore(now, minutesAgo),
    }
    if (kind.device === 'wearable' && (i + k) % 4 === 1) {
      // "I'm OK" pressed 30 s after the alert: cancelled before it escalated.
      return {
        ...base,
        status: 'cancelled',
        cancelled_at: minutesBefore(now, minutesAgo - 0.5),
        cancelled_by_role: 'resident',
      }
    }
    const formerAccount = (i + k) % 7 === 5
    const acknowledge = acknowledgedFirst && {
      acknowledged_at: minutesBefore(now, minutesAgo - 3),
      ...((i + k) % 2 === 0 && { ack_note: 'Synthetic note: the care centre is calling the resident.' }),
    }
    if ((i + k) % 5 === 3) {
      // Cancelled by a caregiver in the portal (after the acknowledge, if there was one).
      return {
        ...base,
        status: 'cancelled',
        ...acknowledge,
        cancelled_at: minutesBefore(now, minutesAgo - 10),
        cancelled_by_role: 'caregiver',
        cancelled_by: formerAccount ? 'Former caregiver' : DEMO_CAREGIVERS[(i + k) % DEMO_CAREGIVERS.length],
      }
    }
    return {
      ...base,
      status: 'resolved',
      ...acknowledge,
      resolved_by: formerAccount ? 'Former staff member' : staff,
      resolved_at: minutesBefore(now, minutesAgo - 25),
    }
  })
}

function wearableDevice(resident: Resident, alerts: ResidentAlert[], i: number): WearableDevice {
  const { wearable } = resident
  const statusTs = Date.parse(wearable.ts)
  return {
    device_id: wearable.device_id,
    device_type: 'wearable',
    online: wearable.online,
    latest: {
      vitals: resident.vitals.map((v, r) => ({ message_id: demoUuid(`${i}1`, r), ...v })),
      // Each fall alert came from a `fall` message with the same `ts` (data-model `event_ts`).
      fall: alerts
        .filter((a) => a.alert_type === 'fall')
        .slice(0, 5)
        .map((a, r) => ({
          message_id: demoUuid(`${i}2`, r),
          ts: a.event_ts,
          confidence: 0.82 + ((i + r) % 4) * 0.04,
          peak_g: Math.round((2.9 + ((i * 3 + r) % 7) * 0.2) * 10) / 10,
          still_s: 15 + ((i + r * 5) % 25),
        })),
      // Heartbeat every 5 min; the battery was a little higher before (lower if charging).
      status: HR_WOBBLE.map((_, r) => ({
        message_id: demoUuid(`${i}3`, r),
        ts: new Date(statusTs - r * 5 * 60_000).toISOString(),
        battery_pct: clamp(wearable.battery_pct + (wearable.charging ? -2 * r : r), 0, 100),
        worn: wearable.worn,
        charging: wearable.charging,
        fw_version: DEMO_FW_VERSION,
      })),
    },
  }
}

const MOTION_MINUTES = [8, 20, 47, 95, 160]
const BED_MINUTES = [15, 62, 130, 410, 480]

/** Bedroom sensor battery for resident index `i`: every fourth sensor is mains-powered (`null`). */
function bedroomBattery(i: number): number | null {
  const seed = SEEDS[i]
  if (seed.bedroomBattery !== undefined) return seed.bedroomBattery
  return i % 4 === 0 ? null : 60 + ((i * 7) % 35)
}

const DEMO_FW_VERSION = '1.0.0'

function bedroomDevice(resident: Resident, alerts: ResidentAlert[], i: number): BedroomDevice {
  const { bedroom } = resident
  const base = Date.parse(bedroom.ts)
  const at = (minutes: number) => new Date(base - minutes * 60_000).toISOString()
  // A possible fall starts with the bed being vacated (data-model `event_ts`), so an
  // open or acknowledged one is the newest `bed` event. A cancelled or resolved one
  // isn't: the resident may be back in bed since.
  const possibleFall = alerts.find((a) => a.alert_type === 'possible_fall' && isOnOpenList(a))
  const bedTimes = possibleFall
    ? [possibleFall.event_ts, ...BED_MINUTES.slice(0, 4).map((m) => minutesBefore(new Date(possibleFall.event_ts), m * 3))]
    : BED_MINUTES.map(at)
  const firstOccupied = possibleFall ? false : i % 3 !== 0

  return {
    device_id: bedroom.device_id,
    device_type: 'bedroom',
    online: bedroom.online,
    latest: {
      motion: MOTION_MINUTES.map((m, r) => ({
        message_id: demoUuid(`${i}4`, r),
        ts: at(m),
        motion: (r % 2 === 0) === (i % 2 === 0),
      })),
      bed: bedTimes.map((ts, r) => ({
        message_id: demoUuid(`${i}5`, r),
        ts,
        occupied: r % 2 === 0 ? firstOccupied : !firstOccupied,
      })),
      status: MOTION_MINUTES.map((_, r) => ({
        message_id: demoUuid(`${i}6`, r),
        ts: at(r * 5),
        battery_pct: bedroomBattery(i),
        fw_version: DEMO_FW_VERSION,
      })),
    },
  }
}

/** Today's thresholds and the changes that led to them, which agree with each other. */
function thresholdsFor(
  n: number,
  now: Date,
): { thresholds: ResidentThresholds; history: ThresholdChangeEntry[] } {
  const history = thresholdChangeSeeds(n).map(
    (seed, k): ThresholdChangeEntry => ({
      change_id: demoUuid(`${n}7`, k),
      changed_at: minutesBefore(now, seed.daysAgo * 24 * 60),
      changed_by: seed.by,
      changes: seed.changes,
    }),
  )
  const values = [...history]
    .reverse()
    .flatMap((entry) => entry.changes)
    .reduce((t, c) => withValue(t, c.field, c.new_value), DEMO_DEFAULT_THRESHOLDS)
  // Without changes the item is as seeded: no `updated_at` / `updated_by` (data-model).
  const newest = history[0]
  return {
    thresholds: {
      ...values,
      ...(newest && { updated_at: newest.changed_at, updated_by: newest.changed_by }),
    },
    history,
  }
}

/**
 * Everything the resident page shows, built from the same data as the dashboards so the
 * two agree. `null` if there is no resident with that ID.
 */
export function createResidentDetail(id: string, now: Date = new Date()): ResidentDetail | null {
  const { residents, alerts: openAlerts } = createDemoData(now)
  const i = residents.findIndex((r) => r.resident_id === id)
  if (i === -1) return null
  const resident = residents[i]
  const alerts = [
    ...openAlerts.filter((a) => a.resident_id === id),
    ...historyAlerts(resident, i, now),
  ].sort((a, b) => b.event_ts.localeCompare(a.event_ts))
  const { thresholds, history } = thresholdsFor(i + 1, now)

  return {
    resident,
    wearable: wearableDevice(resident, alerts, i),
    bedroom: bedroomDevice(resident, alerts, i),
    alerts,
    thresholds,
    threshold_history: history,
  }
}

// ---------------------------------------------------------------------------
// Devices view (patterns 4, 5, 8)

/**
 * Every device in the demo fleet (one wearable and one bedroom sensor per resident),
 * built from the same data as the dashboards and resident pages so they agree.
 */
export function createDeviceFleet(now: Date = new Date()): FleetDevice[] {
  const { residents } = createDemoData(now)
  return residents.flatMap((resident, i): FleetDevice[] => {
    const assigned_to = {
      resident_id: resident.resident_id,
      name: resident.name,
      home_id: resident.home_id,
    }
    return [
      {
        device_id: resident.wearable.device_id,
        device_type: 'wearable',
        assigned_to,
        online: resident.wearable.online,
        last_seen: resident.wearable.ts,
        battery_pct: resident.wearable.battery_pct,
        charging: resident.wearable.charging,
        fw_version: DEMO_FW_VERSION,
      },
      {
        device_id: resident.bedroom.device_id,
        device_type: 'bedroom',
        assigned_to,
        online: resident.bedroom.online,
        last_seen: resident.bedroom.ts,
        battery_pct: bedroomBattery(i),
        charging: false,
        fw_version: DEMO_FW_VERSION,
      },
    ]
  })
}
