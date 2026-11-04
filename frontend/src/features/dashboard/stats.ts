import type {
  BedroomSensorState,
  DashboardData,
  Resident,
  ResidentAlert,
  WearableState,
} from '../../types/dashboard'
import { LOW_BATTERY_DISPLAY_PCT } from './labels'

/** Open or acknowledged: still on the open list (pattern 9). Cancel and resolve take it off. */
export function isOnOpenList(alert: ResidentAlert): boolean {
  return alert.status === 'open' || alert.status === 'acknowledged'
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

export function offlineDevices(residents: readonly Resident[]): OfflineDevice[] {
  return residents.flatMap((resident): OfflineDevice[] => [
    ...(resident.wearable.online ? [] : [{ resident, device: resident.wearable, kind: 'wearable' as const }]),
    ...(resident.bedroom.online ? [] : [{ resident, device: resident.bedroom, kind: 'bedroom' as const }]),
  ])
}

/** Display filter for the low-battery tile. Not an alert threshold. */
export function lowBatteryResidents(residents: readonly Resident[]): Resident[] {
  return residents.filter((r) => r.wearable.battery_pct < LOW_BATTERY_DISPLAY_PCT)
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
