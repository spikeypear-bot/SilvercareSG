import { act, renderHook, waitFor } from '@testing-library/react'
import { demoDataFor } from '../../test/dashboardMock'
import { deferred } from '../../test/fixtures'
import type { DashboardData } from '../../types/dashboard'
import { FAMILY_LINKED_RESIDENT_IDS } from './demoData'
import { DEMO_DELAY_MS, loadDashboardData, useDashboardData, type DashboardLoader } from './useDashboardData'

describe('loadDashboardData (criterion 18)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns a promise that resolves after the ~400 ms simulated delay', async () => {
    vi.useFakeTimers()
    expect(DEMO_DELAY_MS).toBe(400)
    let settled = false
    const promise = loadDashboardData('staff').then((d) => {
      settled = true
      return d
    })
    expect(promise).toBeInstanceOf(Promise)
    await vi.advanceTimersByTimeAsync(399)
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(settled).toBe(true)
  })

  it('staff: all 20 residents and all open alerts (criterion 21)', async () => {
    vi.useFakeTimers()
    const promise = loadDashboardData('staff')
    await vi.advanceTimersByTimeAsync(DEMO_DELAY_MS)
    const data = await promise
    expect(data.residents).toHaveLength(20)
    expect(data.alerts.length).toBeGreaterThan(0)
    expect(data.alerts.every((a) => a.status === 'open')).toBe(true)
  })

  it('family: only the 2 linked residents and their alerts (criterion 21)', async () => {
    vi.useFakeTimers()
    const promise = loadDashboardData('family')
    await vi.advanceTimersByTimeAsync(DEMO_DELAY_MS)
    const data = await promise
    expect(data.residents.map((r) => r.resident_id)).toEqual([...FAMILY_LINKED_RESIDENT_IDS])
    expect(data.alerts.length).toBeGreaterThan(0)
    expect(data.alerts.every((a) => FAMILY_LINKED_RESIDENT_IDS.includes(a.resident_id))).toBe(true)
  })
})

describe('useDashboardData', () => {
  it('is loading, then has data', async () => {
    const pending = deferred<DashboardData>()
    const loader = vi.fn<DashboardLoader>(() => pending.promise)
    const { result } = renderHook(() => useDashboardData('staff', loader))
    expect(result.current).toMatchObject({ loading: true, error: false, data: null })

    const data = demoDataFor('staff')
    await act(async () => pending.resolve(data))
    expect(result.current).toMatchObject({ loading: false, error: false, data })
    expect(loader).toHaveBeenCalledWith('staff')
  })

  it('reports an error and loads again on retry', async () => {
    const loader = vi.fn<DashboardLoader>().mockRejectedValueOnce(new Error('boom'))
    loader.mockResolvedValue(demoDataFor('family'))
    const { result } = renderHook(() => useDashboardData('family', loader))
    await waitFor(() => expect(result.current.error).toBe(true))
    expect(result.current.loading).toBe(false)

    act(() => result.current.retry())
    expect(result.current).toMatchObject({ loading: true, error: false })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe(false)
    expect(result.current.data?.residents).toHaveLength(2)
    expect(loader).toHaveBeenCalledTimes(2)
  })
})
