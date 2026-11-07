import { act, renderHook, waitFor } from '@testing-library/react'
import { deferred } from '../../test/fixtures'
import { fleetDevice } from '../../test/fleetMock'
import type { ApiDevice, FleetDevice } from '../../types/devices'

const api = vi.hoisted(() => ({ getDevices: vi.fn<() => Promise<ApiDevice[]>>() }))
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, getDevices: api.getDevices }
})

import { ApiError } from '../../lib/api'
import { loadDeviceFleet, toFleetDevice, useDeviceFleet, type DeviceFleetLoader } from './useDashboardData'

/** A synthetic `GET /devices` device: an online wearable assigned to Test Resident A. */
function apiDevice(overrides: Partial<ApiDevice> = {}): ApiDevice {
  return {
    device_id: 'wearable-test-001',
    device_type: 'wearable',
    online: true,
    last_seen: '2026-11-07T02:00:00Z',
    battery_pct: 80,
    charging: true,
    fw_version: '1.2.3',
    assigned_to: { resident_id: 'resident-test-001', display_name: 'Test Resident A', home_id: 'home-test-001' },
    ...overrides,
  }
}

describe('toFleetDevice', () => {
  it('maps every field, with display_name as the name', () => {
    expect(toFleetDevice(apiDevice())).toEqual({
      device_id: 'wearable-test-001',
      device_type: 'wearable',
      assigned_to: { resident_id: 'resident-test-001', name: 'Test Resident A', home_id: 'home-test-001' },
      online: true,
      last_seen: '2026-11-07T02:00:00Z',
      battery_pct: 80,
      charging: true,
      fw_version: '1.2.3',
    })
  })

  it('falls back to the resident ID when display_name is null', () => {
    const device = toFleetDevice(
      apiDevice({ assigned_to: { resident_id: 'resident-test-002', display_name: null, home_id: 'home-test-002' } }),
    )
    expect(device.assigned_to).toEqual({
      resident_id: 'resident-test-002',
      name: 'resident-test-002',
      home_id: 'home-test-002',
    })
  })

  it('keeps an empty display_name (only null falls back)', () => {
    const device = toFleetDevice(
      apiDevice({ assigned_to: { resident_id: 'resident-test-002', display_name: '', home_id: 'home-test-002' } }),
    )
    expect(device.assigned_to?.name).toBe('')
  })

  it("maps a null home_id to ''", () => {
    const device = toFleetDevice(
      apiDevice({ assigned_to: { resident_id: 'resident-test-001', display_name: 'Test Resident A', home_id: null } }),
    )
    expect(device.assigned_to).toEqual({ resident_id: 'resident-test-001', name: 'Test Resident A', home_id: '' })
  })

  it('keeps a spare (assigned_to null) as null, with never-reported fields null', () => {
    const spare = apiDevice({
      device_id: 'bedroom-test-spare-001',
      device_type: 'bedroom',
      online: false,
      last_seen: null,
      battery_pct: null,
      charging: false,
      fw_version: null,
      assigned_to: null,
    })
    expect(toFleetDevice(spare)).toEqual({
      device_id: 'bedroom-test-spare-001',
      device_type: 'bedroom',
      assigned_to: null,
      online: false,
      last_seen: null,
      battery_pct: null,
      charging: false,
      fw_version: null,
    })
  })

  it('drops fields the API adds that the view does not know', () => {
    const withExtra = {
      ...apiDevice(),
      serial_number: 'SERIAL-TEST-001',
      assigned_to: {
        resident_id: 'resident-test-001',
        display_name: 'Test Resident A',
        home_id: 'home-test-001',
        date_of_birth: '1900-01-01',
      },
    } as ApiDevice
    const device = toFleetDevice(withExtra)
    expect(device).not.toHaveProperty('serial_number')
    expect(device.assigned_to).not.toHaveProperty('date_of_birth')
    expect(device.assigned_to).not.toHaveProperty('display_name')
    expect(Object.keys(device).sort()).toEqual([
      'assigned_to',
      'battery_pct',
      'charging',
      'device_id',
      'device_type',
      'fw_version',
      'last_seen',
      'online',
    ])
    expect(Object.keys(device.assigned_to ?? {}).sort()).toEqual(['home_id', 'name', 'resident_id'])
  })
})

describe('loadDeviceFleet', () => {
  beforeEach(() => {
    api.getDevices.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('calls getDevices once and maps each API device to a FleetDevice, in order', async () => {
    api.getDevices.mockResolvedValue([
      apiDevice(),
      apiDevice({
        device_id: 'bedroom-test-002',
        device_type: 'bedroom',
        battery_pct: null,
        charging: false,
        assigned_to: { resident_id: 'resident-test-002', display_name: null, home_id: null },
      }),
      apiDevice({ device_id: 'wearable-test-spare-001', assigned_to: null }),
    ])
    const fleet = await loadDeviceFleet()
    expect(api.getDevices).toHaveBeenCalledTimes(1)
    expect(fleet.map((d) => d.device_id)).toEqual([
      'wearable-test-001',
      'bedroom-test-002',
      'wearable-test-spare-001',
    ])
    expect(fleet[0].assigned_to).toEqual({
      resident_id: 'resident-test-001',
      name: 'Test Resident A',
      home_id: 'home-test-001',
    })
    expect(fleet[1].assigned_to).toEqual({ resident_id: 'resident-test-002', name: 'resident-test-002', home_id: '' })
    expect(fleet[2].assigned_to).toBeNull()
  })

  it('does not pass an unknown API field through', async () => {
    api.getDevices.mockResolvedValue([{ ...apiDevice(), internal_note: 'Test only' } as ApiDevice])
    const [device] = await loadDeviceFleet()
    expect(device).not.toHaveProperty('internal_note')
  })

  it('resolves to an empty list for an empty fleet', async () => {
    api.getDevices.mockResolvedValue([])
    await expect(loadDeviceFleet()).resolves.toEqual([])
  })

  it('resolves without the demo delay (no timers needed)', async () => {
    vi.useFakeTimers()
    api.getDevices.mockResolvedValue([apiDevice()])
    // No timers are advanced: it must settle on microtasks alone.
    await expect(loadDeviceFleet()).resolves.toHaveLength(1)
  })

  it('rejects with the ApiError when getDevices rejects', async () => {
    const error = new ApiError(403, 'forbidden')
    api.getDevices.mockRejectedValue(error)
    await expect(loadDeviceFleet()).rejects.toBe(error)
  })

  it('the hook with the default loader shows the error state when getDevices rejects', async () => {
    api.getDevices.mockRejectedValue(new ApiError(0, 'network_error'))
    const { result } = renderHook(() => useDeviceFleet())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current).toMatchObject({ error: true, data: null })
  })

  it('the hook with the default loader shows the mapped devices', async () => {
    api.getDevices.mockResolvedValue([apiDevice()])
    const { result } = renderHook(() => useDeviceFleet())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe(false)
    expect(result.current.data).toEqual([toFleetDevice(apiDevice())])
  })
})

describe('useDeviceFleet', () => {
  it('is loading, then has data', async () => {
    const pending = deferred<FleetDevice[]>()
    const loader = vi.fn<DeviceFleetLoader>(() => pending.promise)
    const { result } = renderHook(() => useDeviceFleet(loader))
    expect(result.current).toMatchObject({ loading: true, error: false, data: null })

    const data = [fleetDevice()]
    await act(async () => pending.resolve(data))
    expect(result.current).toMatchObject({ loading: false, error: false, data })
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('reports an error and loads again on retry', async () => {
    const loader = vi.fn<DeviceFleetLoader>().mockRejectedValueOnce(new Error('boom'))
    loader.mockResolvedValue([fleetDevice()])
    const { result } = renderHook(() => useDeviceFleet(loader))
    await waitFor(() => expect(result.current.error).toBe(true))
    expect(result.current).toMatchObject({ loading: false, data: null })

    act(() => result.current.retry())
    expect(result.current).toMatchObject({ loading: true, error: false })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe(false)
    expect(result.current.data).toHaveLength(1)
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('an empty fleet is data, not an error', async () => {
    const loader = vi.fn<DeviceFleetLoader>().mockResolvedValue([])
    const { result } = renderHook(() => useDeviceFleet(loader))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current).toMatchObject({ error: false, data: [] })
  })

  it('does not load again on re-render', async () => {
    const loader = vi.fn<DeviceFleetLoader>().mockResolvedValue([])
    const { result, rerender } = renderHook(() => useDeviceFleet(loader))
    await waitFor(() => expect(result.current.loading).toBe(false))
    rerender()
    rerender()
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('ignores a result that arrives after unmount', async () => {
    const pending = deferred<FleetDevice[]>()
    const loader = vi.fn<DeviceFleetLoader>(() => pending.promise)
    const { result, unmount } = renderHook(() => useDeviceFleet(loader))
    unmount()
    await act(async () => pending.resolve([fleetDevice()]))
    expect(result.current.data).toBeNull()
  })
})
