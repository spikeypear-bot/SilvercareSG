import type { FleetDevice } from '../../types/devices'
import { LOW_BATTERY_DISPLAY_PCT } from '../dashboard/labels'

export type DeviceFilter = 'all' | 'offline' | 'lowBattery' | 'normal'

/** Below the low-battery display filter (a display filter, not an alert threshold). */
export function isLowBattery(device: FleetDevice): boolean {
  return device.battery_pct !== null && device.battery_pct < LOW_BATTERY_DISPLAY_PCT
}

/** Online with enough battery. */
export function isReportingNormally(device: FleetDevice): boolean {
  return device.online && !isLowBattery(device)
}

/** 0 offline, 1 low battery, 2 the rest. Offline wins over low battery. */
function triageGroup(device: FleetDevice): number {
  if (!device.online) return 0
  return isLowBattery(device) ? 1 : 2
}

/**
 * Problems first: offline (longest silent first; never reported before anything), then
 * low battery (lowest first), then the rest, each by device ID.
 */
export function sortForTriage(devices: readonly FleetDevice[]): FleetDevice[] {
  return [...devices].sort((a, b) => {
    const group = triageGroup(a) - triageGroup(b)
    if (group !== 0) return group
    if (triageGroup(a) === 0) {
      const seen = (a.last_seen ?? '').localeCompare(b.last_seen ?? '')
      if (seen !== 0) return seen
    }
    if (triageGroup(a) === 1) {
      const battery = (a.battery_pct ?? 0) - (b.battery_pct ?? 0)
      if (battery !== 0) return battery
    }
    return a.device_id.localeCompare(b.device_id)
  })
}

export function filterDevices(devices: readonly FleetDevice[], filter: DeviceFilter): FleetDevice[] {
  switch (filter) {
    case 'all':
      return [...devices]
    case 'offline':
      return devices.filter((d) => !d.online)
    case 'lowBattery':
      return devices.filter(isLowBattery)
    case 'normal':
      return devices.filter(isReportingNormally)
  }
}

export interface FleetStats {
  total: number
  offline: number
  /** Includes offline devices whose last reported battery was low. */
  lowBattery: number
  normal: number
}

export function fleetStats(devices: readonly FleetDevice[]): FleetStats {
  return {
    total: devices.length,
    offline: devices.filter((d) => !d.online).length,
    lowBattery: devices.filter(isLowBattery).length,
    normal: devices.filter(isReportingNormally).length,
  }
}

export const DEVICE_TYPE_LABELS = { wearable: 'Wearable', bedroom: 'Bedroom sensor' } as const
