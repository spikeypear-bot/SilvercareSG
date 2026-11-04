import { createDeviceFleet } from '../dashboard/demoData'
import { fleetDevice, minutesAgo, spareDevice } from '../../test/fleetMock'
import type { FleetDevice } from '../../types/devices'
import {
  DEVICE_TYPE_LABELS,
  filterDevices,
  fleetStats,
  isLowBattery,
  isReportingNormally,
  sortForTriage,
} from './fleet'

const NOW = new Date('2026-10-18T02:30:00Z')
const dev = (overrides: Partial<FleetDevice>) => fleetDevice(overrides, NOW)
const ids = (devices: FleetDevice[]) => devices.map((d) => d.device_id)

describe('isLowBattery', () => {
  it.each([
    [0, true],
    [9, true],
    [19, true],
    [20, false],
    [21, false],
    [100, false],
  ])('battery %i%% -> %s', (pct, low) => {
    expect(isLowBattery(dev({ battery_pct: pct }))).toBe(low)
  })

  it('a mains-powered or never-reported device (null battery) is not low', () => {
    expect(isLowBattery(dev({ battery_pct: null }))).toBe(false)
    expect(isLowBattery(spareDevice())).toBe(false)
  })

  it('an offline device with a low last-reported battery is still low', () => {
    expect(isLowBattery(dev({ online: false, battery_pct: 5 }))).toBe(true)
  })

  it('charging does not change it', () => {
    expect(isLowBattery(dev({ battery_pct: 10, charging: true }))).toBe(true)
  })
})

describe('isReportingNormally', () => {
  it('online with battery OK', () => {
    expect(isReportingNormally(dev({ online: true, battery_pct: 20 }))).toBe(true)
  })

  it('online and mains-powered', () => {
    expect(isReportingNormally(dev({ online: true, battery_pct: null }))).toBe(true)
  })

  it('not when offline, whatever the battery', () => {
    expect(isReportingNormally(dev({ online: false, battery_pct: 90 }))).toBe(false)
    expect(isReportingNormally(dev({ online: false, battery_pct: null }))).toBe(false)
  })

  it('not when online with a low battery', () => {
    expect(isReportingNormally(dev({ online: true, battery_pct: 19 }))).toBe(false)
  })
})

describe('sortForTriage', () => {
  it('puts offline first (oldest last_seen first), then low battery (lowest first), then the rest by ID', () => {
    const devices = [
      dev({ device_id: 'd-ok-b', battery_pct: 80 }),
      dev({ device_id: 'd-low-15', battery_pct: 15 }),
      dev({ device_id: 'd-off-16m', online: false, last_seen: minutesAgo(16, NOW) }),
      dev({ device_id: 'd-ok-a', battery_pct: null }),
      dev({ device_id: 'd-low-5', battery_pct: 5 }),
      dev({ device_id: 'd-off-2d', online: false, last_seen: minutesAgo(2 * 24 * 60, NOW) }),
      dev({ device_id: 'd-off-1h', online: false, last_seen: minutesAgo(60, NOW) }),
    ]
    expect(ids(sortForTriage(devices))).toEqual([
      'd-off-2d',
      'd-off-1h',
      'd-off-16m',
      'd-low-5',
      'd-low-15',
      'd-ok-a',
      'd-ok-b',
    ])
  })

  it('a device that never reported (null last_seen) comes before every other offline device', () => {
    const devices = [
      dev({ device_id: 'b-old', online: false, last_seen: minutesAgo(10 * 24 * 60, NOW) }),
      spareDevice({ device_id: 'z-never' }),
      dev({ device_id: 'a-new', online: false, last_seen: minutesAgo(20, NOW) }),
    ]
    expect(ids(sortForTriage(devices))).toEqual(['z-never', 'b-old', 'a-new'])
  })

  it('an offline device with a low battery sorts with the offline devices, by last_seen', () => {
    const devices = [
      dev({ device_id: 'low-online', battery_pct: 3 }),
      dev({ device_id: 'off-low', online: false, battery_pct: 4, last_seen: minutesAgo(30, NOW) }),
      dev({ device_id: 'off-ok', online: false, battery_pct: 90, last_seen: minutesAgo(40, NOW) }),
    ]
    expect(ids(sortForTriage(devices))).toEqual(['off-ok', 'off-low', 'low-online'])
  })

  it('breaks ties by device_id in every group', () => {
    const seen = minutesAgo(30, NOW)
    const devices = [
      dev({ device_id: 'off-b', online: false, last_seen: seen }),
      dev({ device_id: 'ok-b' }),
      dev({ device_id: 'low-b', battery_pct: 10 }),
      dev({ device_id: 'off-a', online: false, last_seen: seen }),
      dev({ device_id: 'ok-a' }),
      dev({ device_id: 'low-a', battery_pct: 10 }),
      spareDevice({ device_id: 'never-b' }),
      spareDevice({ device_id: 'never-a' }),
    ]
    expect(ids(sortForTriage(devices))).toEqual([
      'never-a',
      'never-b',
      'off-a',
      'off-b',
      'low-a',
      'low-b',
      'ok-a',
      'ok-b',
    ])
  })

  it('does not change the input array and returns a new one', () => {
    const devices = [dev({ device_id: 'b' }), dev({ device_id: 'a' })]
    const sorted = sortForTriage(devices)
    expect(ids(devices)).toEqual(['b', 'a'])
    expect(sorted).not.toBe(devices)
  })

  it('handles an empty list', () => {
    expect(sortForTriage([])).toEqual([])
  })
})

describe('filterDevices', () => {
  const devices = [
    dev({ device_id: 'ok' }),
    dev({ device_id: 'mains', battery_pct: null }),
    dev({ device_id: 'low', battery_pct: 12 }),
    dev({ device_id: 'off', online: false }),
    dev({ device_id: 'off-low', online: false, battery_pct: 8 }),
    spareDevice({ device_id: 'spare' }),
  ]

  it('all: every device, as a copy, in the same order', () => {
    const all = filterDevices(devices, 'all')
    expect(ids(all)).toEqual(ids(devices))
    expect(all).not.toBe(devices)
  })

  it('offline: only offline devices, keeping the order', () => {
    expect(ids(filterDevices(devices, 'offline'))).toEqual(['off', 'off-low', 'spare'])
  })

  it('lowBattery: battery below 20 %, including offline ones', () => {
    expect(ids(filterDevices(devices, 'lowBattery'))).toEqual(['low', 'off-low'])
  })

  it('normal: online and not low, including mains-powered', () => {
    expect(ids(filterDevices(devices, 'normal'))).toEqual(['ok', 'mains'])
  })

  it('returns [] for an empty list', () => {
    for (const f of ['all', 'offline', 'lowBattery', 'normal'] as const) {
      expect(filterDevices([], f)).toEqual([])
    }
  })
})

describe('fleetStats', () => {
  it('counts total, offline, low battery (including offline), and normal', () => {
    const devices = [
      dev({ device_id: 'ok' }),
      dev({ device_id: 'low', battery_pct: 12 }),
      dev({ device_id: 'off-low', online: false, battery_pct: 8 }),
      spareDevice(),
    ]
    expect(fleetStats(devices)).toEqual({ total: 4, offline: 2, lowBattery: 2, normal: 1 })
  })

  it('is all zeros for an empty fleet', () => {
    expect(fleetStats([])).toEqual({ total: 0, offline: 0, lowBattery: 0, normal: 0 })
  })

  it('counts the demo fleet: 40 total, 6 offline, 3 low battery, 31 normal', () => {
    expect(fleetStats(createDeviceFleet(NOW))).toEqual({ total: 40, offline: 6, lowBattery: 3, normal: 31 })
  })

  it('normal is online and not low, so the filters agree with the counts', () => {
    const fleet = createDeviceFleet(NOW)
    const stats = fleetStats(fleet)
    expect(filterDevices(fleet, 'offline')).toHaveLength(stats.offline)
    expect(filterDevices(fleet, 'lowBattery')).toHaveLength(stats.lowBattery)
    expect(filterDevices(fleet, 'normal')).toHaveLength(stats.normal)
    expect(fleet.filter((d) => d.online && !isLowBattery(d))).toHaveLength(stats.normal)
  })
})

describe('DEVICE_TYPE_LABELS', () => {
  it('names each device type', () => {
    expect(DEVICE_TYPE_LABELS).toEqual({ wearable: 'Wearable', bedroom: 'Bedroom sensor' })
  })
})
