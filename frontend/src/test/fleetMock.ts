/**
 * Controllable loader for `useDeviceFleet`, plus synthetic device fixtures. Usage in a
 * test file under src/features/<x>/:
 *
 *   vi.mock('../dashboard/useDashboardData', async (importOriginal) => {
 *     const actual = await importOriginal<typeof import('../dashboard/useDashboardData')>()
 *     const { deviceFleetLoader } = await import('../../test/fleetMock')
 *     return { ...actual, useDeviceFleet: () => actual.useDeviceFleet(deviceFleetLoader) }
 *   })
 */
import { vi } from 'vitest'
import { createDeviceFleet } from '../features/dashboard/demoData'
import type { DeviceFleetLoader } from '../features/dashboard/useDashboardData'
import type { FleetDevice } from '../types/devices'

export const deviceFleetLoader = vi.fn<DeviceFleetLoader>()

/** Demo fleet without the 400 ms delay. */
export function resetDeviceFleetLoader(): void {
  deviceFleetLoader.mockReset()
  deviceFleetLoader.mockImplementation(async () => createDeviceFleet(new Date()))
}

/** A synthetic device; online, 80 %, assigned to Test Resident A, seen 2 min ago. */
export function fleetDevice(overrides: Partial<FleetDevice> = {}, now: Date = new Date()): FleetDevice {
  return {
    device_id: 'wearable-test-001',
    device_type: 'wearable',
    assigned_to: { resident_id: 'resident-test-001', name: 'Test Resident A', home_id: 'home-test-001' },
    online: true,
    last_seen: new Date(now.getTime() - 2 * 60_000).toISOString(),
    battery_pct: 80,
    charging: false,
    fw_version: '1.0.0',
    ...overrides,
  }
}

/** An unassigned spare that has never reported: no last_seen, battery, or firmware. */
export function spareDevice(overrides: Partial<FleetDevice> = {}): FleetDevice {
  return fleetDevice({
    device_id: 'bedroom-test-spare-001',
    device_type: 'bedroom',
    assigned_to: null,
    online: false,
    last_seen: null,
    battery_pct: null,
    charging: false,
    fw_version: null,
    ...overrides,
  })
}

/** ISO time `minutes` before `now`. */
export function minutesAgo(minutes: number, now: Date = new Date()): string {
  return new Date(now.getTime() - minutes * 60_000).toISOString()
}
