import type {
  AlertType,
  BedroomSensorState,
  DashboardData,
  Resident,
  ResidentAlert,
  WearableState,
} from '../../types/dashboard'
import { LOW_BATTERY_DISPLAY_PCT } from './labels'

const DEVICE_PROBLEM_TYPES: ReadonlySet<AlertType> = new Set<AlertType>(['device_offline', 'battery_low', 'not_worn'])

/**
 * Device problems (docs/features/watchdog.md) are acknowledged by tech staff or the
 * resident's caregivers, never by the nurse; health alerts (every other type) only by the
 * nurse (user's decision 2026-11-05; docs/data-model.md, Status updates).
 */
export function isDeviceProblem(alert: Pick<ResidentAlert, 'alert_type'>): boolean {
  return DEVICE_PROBLEM_TYPES.has(alert.alert_type)
}

/** Open or acknowledged: still on the open list (pattern 9). Cancel and resolve take it off. */
export function isOnOpenList(alert: ResidentAlert): boolean {
  return alert.status === 'open' || alert.status === 'acknowledged'
}

/**
 * Critical alerts and alerts from unassigned devices are emailed to the care centre
 * nurse at once; warnings only after 5 minutes (docs/features/alert-routing.md).
 */
export function sentToNurseAtOnce(alert: ResidentAlert): boolean {
  return alert.severity === 'critical' || alert.resident_id === 'unassigned'
}

/** The alerts the staff dashboards list: open and acknowledged ones. */
export function openListAlerts(alerts: readonly ResidentAlert[]): ResidentAlert[] {
  return alerts.filter(isOnOpenList)
}

/** Critical first, then newest first within each group. */
export function sortAlertsForInbox(alerts: readonly ResidentAlert[]): ResidentAlert[] {
  return [...alerts].sort((a, b) => {
    const critical = Number(b.severity === 'critical') - Number(a.severity === 'critical')
    return critical || b.event_ts.localeCompare(a.event_ts)
  })
}

export interface OfflineDevice {
  resident: Resident
  device: WearableState | BedroomSensorState
  kind: 'wearable' | 'bedroom'
}

/** Assigned devices that are offline (one that has never reported counts). */
export function offlineDevices(residents: readonly Resident[]): OfflineDevice[] {
  return residents.flatMap((resident): OfflineDevice[] => {
    const { wearable, bedroom } = resident
    return [
      ...(wearable && !wearable.online ? [{ resident, device: wearable, kind: 'wearable' as const }] : []),
      ...(bedroom && !bedroom.online ? [{ resident, device: bedroom, kind: 'bedroom' as const }] : []),
    ]
  })
}

/** A resident whose wearable has reported a battery level. */
export type ResidentWithBattery = Resident & { wearable: WearableState & { battery_pct: number } }

/** Display filter for the low-battery tile. Not an alert threshold. Unknown levels are left out. */
export function lowBatteryResidents(residents: readonly Resident[]): ResidentWithBattery[] {
  return residents.filter(
    (r): r is ResidentWithBattery =>
      r.wearable !== null && r.wearable.battery_pct !== null && r.wearable.battery_pct < LOW_BATTERY_DISPLAY_PCT,
  )
}

export interface DashboardStats {
  residents: number
  openAlerts: number
  criticalAlerts: number
  devicesOffline: number
  lowBattery: number
}

export function dashboardStats(data: DashboardData): DashboardStats {
  const open = openListAlerts(data.alerts)
  return {
    residents: data.residents.length,
    openAlerts: open.length,
    criticalAlerts: open.filter((a) => a.severity === 'critical').length,
    devicesOffline: offlineDevices(data.residents).length,
    lowBattery: lowBatteryResidents(data.residents).length,
  }
}
