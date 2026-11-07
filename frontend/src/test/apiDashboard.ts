/**
 * Synthetic `GET /dashboard` bodies, shaped exactly as the API sends them (null, never
 * missing keys; lambdas/api-dashboard/). For tests of `toResident` / `toResidentAlert`
 * and of the dashboards rendered from API-shaped data.
 */
import type {
  ApiDashboardAlert,
  ApiDashboardResident,
  BedroomSensorState,
  WearableState,
} from '../types/dashboard'

type ResidentOverrides = Partial<Omit<ApiDashboardResident, 'wearable' | 'bedroom'>> & {
  wearable?: Partial<WearableState> | null
  bedroom?: Partial<BedroomSensorState> | null
}

/** Resident `n` (1 -> resident-test-001, "Test Resident A") with an online wearable and bedroom unit. */
export function apiResident(n: number, overrides: ResidentOverrides = {}): ApiDashboardResident {
  const id = String(n).padStart(3, '0')
  const ts = '2026-11-07T02:00:00Z'
  const { wearable, bedroom, ...rest } = overrides
  return {
    resident_id: `resident-test-${id}`,
    display_name: `Test Resident ${String.fromCharCode(64 + n)}`,
    home_id: `home-test-${id}`,
    status: 'ok',
    status_text: 'Synthetic status: no issues.',
    wearable:
      wearable === null
        ? null
        : {
            device_id: `wearable-test-${id}`,
            ts,
            battery_pct: 80,
            worn: true,
            charging: false,
            online: true,
            ...wearable,
          },
    bedroom:
      bedroom === null ? null : { device_id: `bedroom-test-${id}`, ts, online: true, ...bedroom },
    vitals: [{ ts, hr_bpm: 72, spo2_pct: 97, skin_temp_c: 33.2 }],
    ...rest,
  }
}

/** An open alert with every optional value null, as the API sends it. */
export function apiAlert(overrides: Partial<ApiDashboardAlert> = {}): ApiDashboardAlert {
  return {
    alert_id: 'alert-test-001',
    resident_id: 'resident-test-001',
    device_id: 'wearable-test-001',
    alert_type: 'fall',
    severity: 'critical',
    status: 'open',
    event_ts: '2026-11-07T02:00:00Z',
    acknowledged_at: null,
    acknowledged_by_role: null,
    acknowledged_by: null,
    ack_note: null,
    escalated_at: null,
    escalates_at: null,
    ...overrides,
  }
}
