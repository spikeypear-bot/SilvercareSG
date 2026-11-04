import { act, renderHook, waitFor } from '@testing-library/react'
import { demoDataFor } from '../../test/dashboardMock'
import { deferred } from '../../test/fixtures'
import { demoDetail } from '../../test/residentMock'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import type { ResidentDetail, VitalsThresholds } from '../../types/resident'
import {
  DEMO_DELAY_MS,
  loadResidentDetail,
  useDashboardData,
  useResidentDetail,
  type DashboardLoader,
  type DashboardView,
  type ResidentDetailLoader,
  type ResidentDetailState,
} from './useDashboardData'

const NOW = new Date('2026-10-18T02:30:00Z')
const ACTION_TIME = new Date('2026-10-18T02:31:00Z')

afterEach(() => {
  vi.useRealTimers()
})

describe('loadResidentDetail', () => {
  it('resolves after the simulated delay with the resident, or null for an unknown ID', async () => {
    vi.useFakeTimers()
    let settled = false
    const promise = loadResidentDetail('res-0003').then((d) => {
      settled = true
      return d
    })
    await vi.advanceTimersByTimeAsync(DEMO_DELAY_MS - 1)
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect((await promise)?.resident.name).toBe('Test Resident C')

    const missing = loadResidentDetail('res-9999')
    await vi.advanceTimersByTimeAsync(DEMO_DELAY_MS)
    expect(await missing).toBeNull()
  })
})

describe('useResidentDetail', () => {
  it('is loading, then has data', async () => {
    const pending = deferred<ResidentDetail | null>()
    const loader = vi.fn<ResidentDetailLoader>(() => pending.promise)
    const { result } = renderHook(() => useResidentDetail('res-0001', loader))
    expect(result.current).toMatchObject({ loading: true, error: false, notFound: false, data: null })

    const detail = demoDetail('res-0001', NOW)
    await act(async () => pending.resolve(detail))
    expect(result.current).toMatchObject({ loading: false, error: false, notFound: false, data: detail })
    expect(loader).toHaveBeenCalledWith('res-0001')
  })

  it('reports notFound when the loader returns null', async () => {
    const loader = vi.fn<ResidentDetailLoader>().mockResolvedValue(null)
    const { result } = renderHook(() => useResidentDetail('res-9999', loader))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current).toMatchObject({ notFound: true, error: false, data: null })
  })

  it('reports an error (not notFound) and loads again on retry', async () => {
    const loader = vi.fn<ResidentDetailLoader>().mockRejectedValueOnce(new Error('boom'))
    loader.mockResolvedValue(demoDetail('res-0002', NOW))
    const { result } = renderHook(() => useResidentDetail('res-0002', loader))
    await waitFor(() => expect(result.current.error).toBe(true))
    expect(result.current).toMatchObject({ loading: false, notFound: false, data: null })

    act(() => result.current.retry())
    expect(result.current).toMatchObject({ loading: true, error: false, notFound: false })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe(false)
    expect(result.current.data?.resident.resident_id).toBe('res-0002')
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('a changed residentId shows loading again instead of the old data', async () => {
    const first = deferred<ResidentDetail | null>()
    const second = deferred<ResidentDetail | null>()
    const loader = vi.fn<ResidentDetailLoader>()
    loader.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { result, rerender } = renderHook(({ id }) => useResidentDetail(id, loader), {
      initialProps: { id: 'res-0001' },
    })
    await act(async () => first.resolve(demoDetail('res-0001', NOW)))
    expect(result.current.data?.resident.resident_id).toBe('res-0001')

    rerender({ id: 'res-0002' })
    expect(result.current).toMatchObject({ loading: true, error: false, notFound: false, data: null })
    expect(loader).toHaveBeenLastCalledWith('res-0002')

    await act(async () => second.resolve(demoDetail('res-0002', NOW)))
    expect(result.current.data?.resident.resident_id).toBe('res-0002')
  })

  it('ignores a slow response for the previous residentId', async () => {
    const first = deferred<ResidentDetail | null>()
    const second = deferred<ResidentDetail | null>()
    const loader = vi.fn<ResidentDetailLoader>()
    loader.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { result, rerender } = renderHook(({ id }) => useResidentDetail(id, loader), {
      initialProps: { id: 'res-0001' },
    })
    rerender({ id: 'res-0002' })
    await act(async () => first.resolve(demoDetail('res-0001', NOW)))
    expect(result.current).toMatchObject({ loading: true, data: null })

    await act(async () => second.resolve(demoDetail('res-0002', NOW)))
    expect(result.current.data?.resident.resident_id).toBe('res-0002')
  })
})

describe('useResidentDetail actions (local state only)', () => {
  async function loaded(id = 'res-0003') {
    const data = demoDetail(id, NOW)
    const loader = vi.fn<ResidentDetailLoader>().mockResolvedValue(data)
    const hook = renderHook(() => useResidentDetail(id, loader))
    await waitFor(() => expect(hook.result.current.data).not.toBeNull())
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(ACTION_TIME)
    return { ...hook, data, loader }
  }

  type HookResult = { current: ResidentDetailState }
  const find = (result: HookResult, id: string) =>
    result.current.data?.alerts.find((a) => a.alert_id === id) as ResidentAlert
  const firstWith = (data: ResidentDetail, status: ResidentAlert['status']) =>
    data.alerts.find((a) => a.status === status) as ResidentAlert

  it('acknowledge changes only that alert and does not reload', async () => {
    const { result, data, loader } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.acknowledgeAlert(target.alert_id, 'Test Nurse D'))
    expect(find(result, target.alert_id)).toEqual({
      ...target,
      status: 'acknowledged',
      acknowledged_by: 'Test Nurse D',
      acknowledged_at: ACTION_TIME.toISOString(),
    })
    const after = result.current.data as ResidentDetail
    after.alerts.forEach((a, i) => {
      if (a.alert_id !== target.alert_id) expect(a).toBe(data.alerts[i])
    })
    expect(after.resident).toBe(data.resident)
    expect(after.wearable).toBe(data.wearable)
    expect(after.bedroom).toBe(data.bedroom)
    expect(after.thresholds).toBe(data.thresholds)
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('resolve marks an open alert resolved, and does nothing to an already-resolved one', async () => {
    const { result, data } = await loaded()
    const open = firstWith(data, 'open')
    act(() => result.current.resolveAlert(open.alert_id, 'Test Nurse D'))
    expect(find(result, open.alert_id)).toMatchObject({
      status: 'resolved',
      resolved_by: 'Test Nurse D',
      resolved_at: ACTION_TIME.toISOString(),
    })

    const history = firstWith(data, 'resolved')
    act(() => result.current.resolveAlert(history.alert_id, 'Test Nurse D'))
    act(() => result.current.acknowledgeAlert(history.alert_id, 'Test Nurse D'))
    expect(find(result, history.alert_id)).toBe(history)
    expect(result.current.data?.alerts).toHaveLength(data.alerts.length)
  })

  it('resolve and acknowledge do nothing to a cancelled history alert (res-0002)', async () => {
    const { result, data } = await loaded('res-0002')
    const cancelled = firstWith(data, 'cancelled')
    expect(cancelled).toBeDefined()
    act(() => result.current.resolveAlert(cancelled.alert_id, 'Test Nurse D'))
    act(() => result.current.acknowledgeAlert(cancelled.alert_id, 'Test Nurse D'))
    expect(find(result, cancelled.alert_id)).toBe(cancelled)
    expect(find(result, cancelled.alert_id)).not.toHaveProperty('resolved_by')
    expect(find(result, cancelled.alert_id)).not.toHaveProperty('acknowledged_by')
    result.current.data?.alerts.forEach((a, i) => expect(a).toBe(data.alerts[i]))
  })

  it('updateThresholds replaces the values and sets updated_by/updated_at, leaving the rest', async () => {
    const { result, data, loader } = await loaded('res-0002')
    const values: VitalsThresholds = {
      hr_bpm: { min: 55, max: 115 },
      spo2_pct: { min: 90 },
      skin_temp_c: { min: 30.5, max: 37.0 },
    }
    act(() => result.current.updateThresholds(values, 'Test Doctor B'))
    const after = result.current.data as ResidentDetail
    expect(after.thresholds).toEqual({
      ...values,
      updated_by: 'Test Doctor B',
      updated_at: ACTION_TIME.toISOString(),
    })
    expect(after.alerts).toBe(data.alerts)
    // The server writes history entries; a local save must not add one.
    expect(after.threshold_history).toBe(data.threshold_history)
    expect(after.threshold_history).toHaveLength(1)
    expect(after.resident).toBe(data.resident)
    expect(after.wearable).toBe(data.wearable)
    expect(after.bedroom).toBe(data.bedroom)
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('updateThresholds sets updated_by/updated_at on a resident that had none (no history)', async () => {
    const { result, data } = await loaded('res-0003')
    expect(data.thresholds).not.toHaveProperty('updated_by')
    expect(data.thresholds).not.toHaveProperty('updated_at')
    expect(data.threshold_history).toEqual([])
    act(() => result.current.updateThresholds(data.thresholds, 'Test Doctor A'))
    expect((result.current.data as ResidentDetail).thresholds).toMatchObject({
      updated_by: 'Test Doctor A',
      updated_at: ACTION_TIME.toISOString(),
    })
  })

  it('actions are no-ops before data loads', async () => {
    const pending = deferred<ResidentDetail | null>()
    const loader = vi.fn<ResidentDetailLoader>(() => pending.promise)
    const { result } = renderHook(() => useResidentDetail('res-0003', loader))
    const data = demoDetail('res-0003', NOW)
    act(() => {
      result.current.acknowledgeAlert(data.alerts[0].alert_id, 'Test Nurse D')
      result.current.resolveAlert(data.alerts[0].alert_id, 'Test Nurse D')
      result.current.updateThresholds(data.thresholds, 'Test Doctor B')
    })
    expect(result.current).toMatchObject({ data: null, loading: true, notFound: false })
    await act(async () => pending.resolve(data))
    expect(result.current.data).toBe(data)
  })

  it('actions on a not-found resident keep it not found', async () => {
    const loader = vi.fn<ResidentDetailLoader>().mockResolvedValue(null)
    const { result } = renderHook(() => useResidentDetail('res-9999', loader))
    await waitFor(() => expect(result.current.notFound).toBe(true))
    act(() => result.current.updateThresholds(demoDetail('res-0001', NOW).thresholds, 'Test Doctor B'))
    expect(result.current).toMatchObject({ data: null, notFound: true })
  })
})

describe('useDashboardData after the useLoaded refactor', () => {
  it('a changed view shows loading again and ignores the old response', async () => {
    const staff = deferred<DashboardData>()
    const family = deferred<DashboardData>()
    const loader = vi.fn<DashboardLoader>()
    loader.mockReturnValueOnce(staff.promise).mockReturnValueOnce(family.promise)
    const { result, rerender } = renderHook(({ view }: { view: DashboardView }) => useDashboardData(view, loader), {
      initialProps: { view: 'staff' },
    })
    await act(async () => staff.resolve(demoDataFor('staff', NOW)))
    expect(result.current.data?.residents).toHaveLength(20)

    rerender({ view: 'family' })
    expect(result.current).toMatchObject({ loading: true, error: false, data: null })
    await act(async () => family.resolve(demoDataFor('family', NOW)))
    expect(result.current.data?.residents).toHaveLength(2)
    expect(loader.mock.calls).toEqual([['staff'], ['family']])
  })

  it('uses the latest loader passed in', async () => {
    const first = vi.fn<DashboardLoader>().mockResolvedValue(demoDataFor('staff', NOW))
    const second = vi.fn<DashboardLoader>().mockResolvedValue(demoDataFor('staff', NOW))
    const { result, rerender } = renderHook(({ loader }) => useDashboardData('staff', loader), {
      initialProps: { loader: first },
    })
    await waitFor(() => expect(result.current.data).not.toBeNull())
    rerender({ loader: second })
    act(() => result.current.retry())
    await waitFor(() => expect(second).toHaveBeenCalledTimes(1))
    expect(first).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(result.current.loading).toBe(false))
  })
})
