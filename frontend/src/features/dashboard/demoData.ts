/**
 * Synthetic demo data for the main page. No real resident information.
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

/** The demo residents linked to every caregiver/family account. */
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
  wearableOnline: boolean
  bedroomOnline: boolean
  /** Minutes since the newest vitals reading. */
  vitalsAgeMin: number
}

const ok = (overrides: Partial<ResidentSeed> = {}): ResidentSeed => ({
  status: 'ok',
  hr: 72,
  spo2: 97,
  temp: 33.6,
  activity: 18,
  battery: 80,
  worn: true,
  charging: false,
  wearableOnline: true,
  bedroomOnline: true,
  vitalsAgeMin: 1,
  ...overrides,
})

// One entry per resident, in resident-ID order (Test Resident A = res-0001, ...).
const SEEDS: readonly ResidentSeed[] = [
  ok({ hr: 68, battery: 82, activity: 22 }), // A
  ok({ status: 'attention', hr: 64, activity: 0, battery: 64, temp: 33.1 }), // B (family)
  ok({ status: 'urgent', hr: 96, spo2: 95, activity: 3, battery: 71 }), // C
  ok({ hr: 75, spo2: 98, battery: 45, activity: 30 }), // D
  ok({ hr: 70, spo2: 97, temp: 34.0, battery: 91, activity: 26 }), // E (family)
  ok({ status: 'attention', hr: 78, battery: 15, activity: 12 }), // F
  ok({ status: 'urgent', hr: 128, spo2: 94, temp: 34.6, battery: 77, activity: 5 }), // G
  ok({ hr: 66, battery: 77, activity: 9 }), // H
  ok({ status: 'attention', hr: 74, battery: 69, bedroomOnline: false }), // I
  ok({ hr: 71, battery: 58, worn: false, charging: true, vitalsAgeMin: 35 }), // J
  ok({ hr: 80, spo2: 96, battery: 88, activity: 41 }), // K
  ok({ status: 'attention', hr: 88, battery: 54, activity: 2 }), // L
  ok({ hr: 69, battery: 12, activity: 15 }), // M
  ok({ hr: 73, spo2: 99, battery: 69, activity: 20 }), // N
  ok({ status: 'attention', hr: 76, battery: 38, wearableOnline: false, vitalsAgeMin: 52 }), // O
  ok({ hr: 62, battery: 95, temp: 33.0, activity: 7 }), // P
  ok({ status: 'urgent', hr: 102, spo2: 95, battery: 60, activity: 1 }), // Q
  ok({ hr: 77, battery: 73, activity: 33 }), // R
  ok({ status: 'attention', hr: 82, spo2: 91, battery: 66, activity: 10 }), // S
  ok({ hr: 70, battery: 27, activity: 24 }), // T
]

interface AlertSeed {
  resident: number // 1-based resident number
  type: AlertType
  severity: AlertSeverity
  device: 'wearable' | 'bedroom'
  minutesAgo: number
}

const ALERT_SEEDS: readonly AlertSeed[] = [
  { resident: 3, type: 'fall', severity: 'critical', device: 'wearable', minutesAgo: 4 },
  { resident: 7, type: 'vitals', severity: 'critical', device: 'wearable', minutesAgo: 9 },
  { resident: 12, type: 'possible_fall', severity: 'warning', device: 'bedroom', minutesAgo: 15 },
  { resident: 17, type: 'fall', severity: 'critical', device: 'wearable', minutesAgo: 22 },
  { resident: 19, type: 'vitals', severity: 'warning', device: 'wearable', minutesAgo: 27 },
  { resident: 2, type: 'inactivity', severity: 'warning', device: 'bedroom', minutesAgo: 38 },
  { resident: 15, type: 'device_offline', severity: 'warning', device: 'wearable', minutesAgo: 48 },
  { resident: 6, type: 'low_battery', severity: 'warning', device: 'wearable', minutesAgo: 55 },
  { resident: 9, type: 'device_offline', severity: 'warning', device: 'bedroom', minutesAgo: 70 },
  { resident: 13, type: 'low_battery', severity: 'info', device: 'wearable', minutesAgo: 125 },
  { resident: 2, type: 'possible_fall', severity: 'info', device: 'bedroom', minutesAgo: 410 },
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

export const residentId = (n: number) => `res-${pad4(n)}`

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
      status: seed.status,
      status_text: STATUS_TEXT[seed.status],
      wearable: {
        device_id: `wearable-${pad4(wearableNumber(i))}`,
        ts: minutesBefore(now, seed.wearableOnline ? 2 : seed.vitalsAgeMin),
        battery_pct: seed.battery,
        worn: seed.worn,
        charging: seed.charging,
        online: seed.wearableOnline,
      },
      bedroom: {
        device_id: `bedroom-${pad4(bedroomNumber(i))}`,
        ts: minutesBefore(now, seed.bedroomOnline ? 3 : 70),
        online: seed.bedroomOnline,
      },
      vitals,
    }
  })

  const alerts: ResidentAlert[] = ALERT_SEEDS.map((seed, i): ResidentAlert => {
    const resident = residents[seed.resident - 1]
    return {
      alert_id: `6f1c2e0a-5b3d-5e8f-9a47-${String(i + 1).padStart(12, '0')}`,
      resident_id: resident.resident_id,
      device_id: seed.device === 'wearable' ? resident.wearable.device_id : resident.bedroom.device_id,
      alert_type: seed.type,
      severity: seed.severity,
      status: 'open',
      event_ts: minutesBefore(now, seed.minutesAgo),
    }
  }).sort((a, b) => b.event_ts.localeCompare(a.event_ts))

  return { residents, alerts }
}
