import { act, renderHook, waitFor } from '@testing-library/react'
import { deferred } from '../../test/fixtures'
import { fleetDevice } from '../../test/fleetMock'
import type { FleetDevice } from '../../types/devices'
import { createDeviceFleet } from './demoData'
import { DEMO_DELAY_MS, loadDeviceFleet, useDeviceFleet, type DeviceFleetLoader } from './useDashboardData'

describe('loadDeviceFleet', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('resolves after the ~400 ms simulated delay with the 40-device demo fleet', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-18T02:30:00Z'))
    let settled = false
    const promise = loadDeviceFleet().then((d) => {
      settled = true
      return d
    })
    await vi.advanceTimersByTimeAsync(DEMO_DELAY_MS - 1)
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(settled).toBe(true)
    const fleet = await promise
    expect(fleet).toHaveLength(40)
    expect(fleet).toEqual(createDeviceFleet(new Date()))
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
