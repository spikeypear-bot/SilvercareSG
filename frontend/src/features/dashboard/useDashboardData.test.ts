import { act, renderHook, waitFor } from '@testing-library/react'
import { demoDataFor } from '../../test/dashboardMock'
import { deferred } from '../../test/fixtures'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import { FAMILY_LINKED_RESIDENT_IDS } from './demoData'
import {
  DEMO_DELAY_MS,
  loadDashboardData,
  useDashboardData,
  type DashboardDataState,
  type DashboardLoader,
} from './useDashboardData'

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

  it('staff: all 20 residents and every unresolved alert, open and acknowledged (criterion 21)', async () => {
    vi.useFakeTimers()
    const promise = loadDashboardData('staff')
    await vi.advanceTimersByTimeAsync(DEMO_DELAY_MS)
    const data = await promise
    expect(data.residents).toHaveLength(20)
    expect(data.alerts).toHaveLength(12)
    expect(data.alerts.some((a) => a.status === 'open')).toBe(true)
    expect(data.alerts.some((a) => a.status === 'acknowledged')).toBe(true)
    expect(data.alerts.some((a) => a.status === 'resolved')).toBe(false)
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

describe('acknowledgeAlert and resolveAlert (local state only)', () => {
  const NOW = new Date('2026-10-18T02:30:00Z')
  const ACTION_TIME = new Date('2026-10-18T02:31:00Z')

  afterEach(() => {
    vi.useRealTimers()
  })

  async function loaded() {
    const data = demoDataFor('staff', NOW)
    const loader = vi.fn<DashboardLoader>().mockResolvedValue(data)
    const hook = renderHook(() => useDashboardData('staff', loader))
    await waitFor(() => expect(hook.result.current.data).not.toBeNull())
    // Fake only Date so the actions get a known timestamp; promises and timers stay real.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(ACTION_TIME)
    return { ...hook, data }
  }

  type HookResult = { current: DashboardDataState }
  const alertsOf = (result: HookResult) => result.current.data?.alerts ?? []
  const find = (result: HookResult, id: string) =>
    alertsOf(result).find((a) => a.alert_id === id) as ResidentAlert
  const firstWith = (data: DashboardData, status: ResidentAlert['status']) =>
    data.alerts.find((a) => a.status === status) as ResidentAlert

  it('acknowledge: open -> acknowledged, with by and at', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.acknowledgeAlert(target.alert_id, 'Test Nurse A'))
    expect(find(result, target.alert_id)).toEqual({
      ...target,
      status: 'acknowledged',
      acknowledged_by: 'Test Nurse A',
      acknowledged_at: ACTION_TIME.toISOString(),
    })
  })

  it('acknowledge leaves every other alert untouched (same objects)', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.acknowledgeAlert(target.alert_id, 'Test Nurse A'))
    alertsOf(result).forEach((a, i) => {
      if (a.alert_id !== target.alert_id) expect(a).toBe(data.alerts[i])
    })
    expect(result.current.data?.residents).toBe(data.residents)
  })

  it('acknowledge is a no-op on an already-acknowledged alert (keeps the original by/at)', async () => {
    const { result, data } = await loaded()
    const acked = firstWith(data, 'acknowledged')
    act(() => result.current.acknowledgeAlert(acked.alert_id, 'Test Nurse A'))
    expect(find(result, acked.alert_id)).toBe(acked)
    expect(find(result, acked.alert_id).acknowledged_by).toMatch(/^Test Nurse [BC]$/)
  })

  it('acknowledge is a no-op on a resolved alert', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    const resolved = find(result, target.alert_id)
    act(() => result.current.acknowledgeAlert(target.alert_id, 'Test Nurse D'))
    expect(find(result, target.alert_id)).toBe(resolved)
    expect(resolved.status).toBe('resolved')
    expect(resolved.acknowledged_by).toBeUndefined()
  })

  it('resolve: open -> resolved, with by and at', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    expect(find(result, target.alert_id)).toEqual({
      ...target,
      status: 'resolved',
      resolved_by: 'Test Nurse A',
      resolved_at: ACTION_TIME.toISOString(),
    })
  })

  it('resolve: acknowledged -> resolved, keeping the acknowledgement', async () => {
    const { result, data } = await loaded()
    const acked = firstWith(data, 'acknowledged')
    act(() => result.current.resolveAlert(acked.alert_id, 'Test Nurse A'))
    expect(find(result, acked.alert_id)).toMatchObject({
      status: 'resolved',
      acknowledged_by: acked.acknowledged_by,
      acknowledged_at: acked.acknowledged_at,
      resolved_by: 'Test Nurse A',
      resolved_at: ACTION_TIME.toISOString(),
    })
  })

  it('resolving twice keeps the first resolver', async () => {
    const { result, data } = await loaded()
    const target = data.alerts[0]
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    const first = find(result, target.alert_id)
    vi.setSystemTime(new Date(ACTION_TIME.getTime() + 60_000))
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse D'))
    expect(find(result, target.alert_id)).toBe(first)
    expect(first.resolved_by).toBe('Test Nurse A')
  })

  it('resolve leaves every other alert untouched', async () => {
    const { result, data } = await loaded()
    const target = data.alerts[1]
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    expect(alertsOf(result)).toHaveLength(data.alerts.length)
    alertsOf(result).forEach((a, i) => {
      if (a.alert_id !== target.alert_id) expect(a).toBe(data.alerts[i])
    })
  })

  it('an unknown alert ID changes nothing', async () => {
    const { result, data } = await loaded()
    act(() => result.current.acknowledgeAlert('alert-test-missing', 'Test Nurse A'))
    act(() => result.current.resolveAlert('alert-test-missing', 'Test Nurse A'))
    alertsOf(result).forEach((a, i) => expect(a).toBe(data.alerts[i]))
  })

  it('is a no-op before data loads', async () => {
    const pending = deferred<DashboardData>()
    const loader = vi.fn<DashboardLoader>(() => pending.promise)
    const { result } = renderHook(() => useDashboardData('staff', loader))
    const data = demoDataFor('staff', NOW)
    act(() => result.current.acknowledgeAlert(data.alerts[0].alert_id, 'Test Nurse A'))
    act(() => result.current.resolveAlert(data.alerts[0].alert_id, 'Test Nurse A'))
    expect(result.current).toMatchObject({ data: null, loading: true, error: false })

    await act(async () => pending.resolve(data))
    expect(result.current.data).toBe(data)
  })
})
