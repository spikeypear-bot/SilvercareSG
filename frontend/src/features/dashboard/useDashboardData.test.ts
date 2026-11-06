import { act, renderHook, waitFor } from '@testing-library/react'
import { demoDataFor } from '../../test/dashboardMock'
import { deferred } from '../../test/fixtures'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import { FAMILY_LINKED_RESIDENT_IDS } from './demoData'
import { isOnOpenList } from './stats'
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
    expect(data.alerts).toHaveLength(13)
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

  it('acknowledge: open -> acknowledged, with at and no name or note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.acknowledgeAlert(target.alert_id))
    const after = find(result, target.alert_id)
    expect(after).toEqual({
      ...target,
      status: 'acknowledged',
      acknowledged_at: ACTION_TIME.toISOString(),
    })
    // toEqual ignores undefined keys, so check the absent ones explicitly.
    expect(after).not.toHaveProperty('acknowledged_by')
    expect(after).not.toHaveProperty('ack_note')
  })

  it('acknowledgeAlert takes the alert ID and an optional note, never an actor', async () => {
    const { result } = await loaded()
    expect(result.current.acknowledgeAlert).toHaveLength(2)
  })

  // docs/features/alert-actions-ui.md, criteria 7, 9, 13, 17.
  it('acknowledge with a note stores it as ack_note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.acknowledgeAlert(target.alert_id, 'Synthetic note: on the way.\nAbout 8 minutes.'))
    expect(find(result, target.alert_id)).toEqual({
      ...target,
      status: 'acknowledged',
      acknowledged_at: ACTION_TIME.toISOString(),
      ack_note: 'Synthetic note: on the way.\nAbout 8 minutes.',
    })
  })

  it('acknowledge with an empty note stores no ack_note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.acknowledgeAlert(target.alert_id, ''))
    expect(find(result, target.alert_id)).not.toHaveProperty('ack_note')
  })

  it('a second acknowledge keeps the first time and note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.acknowledgeAlert(target.alert_id, 'Synthetic first note'))
    const first = find(result, target.alert_id)
    vi.setSystemTime(new Date(ACTION_TIME.getTime() + 60_000))
    act(() => result.current.acknowledgeAlert(target.alert_id, 'Synthetic second note'))
    expect(find(result, target.alert_id)).toBe(first)
  })

  it('cancel: open -> cancelled by a caregiver, with time and display name', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.cancelAlert(target.alert_id, 'Test Caregiver A'))
    expect(find(result, target.alert_id)).toEqual({
      ...target,
      status: 'cancelled',
      cancelled_at: ACTION_TIME.toISOString(),
      cancelled_by_role: 'caregiver',
      cancelled_by: 'Test Caregiver A',
    })
  })

  it('cancel of an acknowledged alert keeps the acknowledge and its note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'acknowledged')
    expect(target.acknowledged_at).toBeDefined()
    act(() => result.current.cancelAlert(target.alert_id, 'Test Caregiver A'))
    const after = find(result, target.alert_id)
    expect(after.status).toBe('cancelled')
    expect(after.acknowledged_at).toBe(target.acknowledged_at)
    expect(after.ack_note).toBe(target.ack_note)
  })

  it('cancel does nothing to a cancelled or resolved alert', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    const resolved = find(result, target.alert_id)
    act(() => result.current.cancelAlert(target.alert_id, 'Test Caregiver A'))
    expect(find(result, target.alert_id)).toBe(resolved)

    const other = data.alerts.filter((a) => a.status === 'open')[1]
    act(() => result.current.cancelAlert(other.alert_id, 'Test Caregiver A'))
    const cancelled = find(result, other.alert_id)
    act(() => result.current.cancelAlert(other.alert_id, 'Test Caregiver B'))
    expect(find(result, other.alert_id)).toBe(cancelled)
  })

  it('cancel with an empty display name stores no cancelled_by', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.cancelAlert(target.alert_id, ''))
    expect(find(result, target.alert_id)).not.toHaveProperty('cancelled_by')
  })

  it('a cancelled alert leaves the open list', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.cancelAlert(target.alert_id, 'Test Caregiver A'))
    expect(isOnOpenList(find(result, target.alert_id))).toBe(false)
  })

  const openDeviceProblem = (data: DashboardData) =>
    data.alerts.find((a) => a.status === 'open' && a.alert_type === 'battery_low') as ResidentAlert

  it("the nurse's acknowledge leaves an open device problem unchanged (nurses acknowledge health alerts only)", async () => {
    const { result, data } = await loaded()
    const target = openDeviceProblem(data)
    act(() => result.current.acknowledgeAlert(target.alert_id))
    expect(find(result, target.alert_id)).toBe(target)
  })

  it('a caregiver acknowledge: open device problem -> acknowledged, with at, role, and the display name', async () => {
    const { result, data } = await loaded()
    const target = openDeviceProblem(data)
    act(() => result.current.acknowledgeDeviceProblem(target.alert_id, 'Test Caregiver A'))
    const after = find(result, target.alert_id)
    expect(after).toEqual({
      ...target,
      status: 'acknowledged',
      acknowledged_at: ACTION_TIME.toISOString(),
      acknowledged_by_role: 'caregiver',
      acknowledged_by: 'Test Caregiver A',
    })
    expect(after).not.toHaveProperty('ack_note')
  })

  it('a caregiver acknowledge leaves a health alert, or a device problem no longer open, unchanged', async () => {
    const { result, data } = await loaded()
    const health = firstWith(data, 'open')
    const acked = data.alerts.find((a) => a.status === 'acknowledged' && a.alert_type === 'battery_low') as ResidentAlert
    expect(health.alert_type).toBe('possible_fall')
    act(() => result.current.acknowledgeDeviceProblem(health.alert_id, 'Test Caregiver A'))
    act(() => result.current.acknowledgeDeviceProblem(acked.alert_id, 'Test Caregiver B'))
    expect(find(result, health.alert_id)).toBe(health)
    expect(find(result, acked.alert_id)).toBe(acked)
  })

  it('acknowledge leaves every other alert untouched (same objects)', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.acknowledgeAlert(target.alert_id))
    alertsOf(result).forEach((a, i) => {
      if (a.alert_id !== target.alert_id) expect(a).toBe(data.alerts[i])
    })
    expect(result.current.data?.residents).toBe(data.residents)
  })

  it('acknowledge is a no-op on an already-acknowledged alert (keeps the original at and note)', async () => {
    const { result, data } = await loaded()
    const acked = firstWith(data, 'acknowledged')
    expect(acked.ack_note).toBeDefined()
    const originalAt = acked.acknowledged_at
    act(() => result.current.acknowledgeAlert(acked.alert_id))
    expect(find(result, acked.alert_id)).toBe(acked)
    expect(find(result, acked.alert_id).acknowledged_at).toBe(originalAt)
    expect(find(result, acked.alert_id).acknowledged_at).not.toBe(ACTION_TIME.toISOString())
    expect(find(result, acked.alert_id).ack_note).toBe(acked.ack_note)
  })

  it('acknowledge is a no-op on a resolved alert', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.resolveAlert(target.alert_id))
    const resolved = find(result, target.alert_id)
    act(() => result.current.acknowledgeAlert(target.alert_id))
    expect(find(result, target.alert_id)).toBe(resolved)
    expect(resolved.status).toBe('resolved')
    expect(resolved).not.toHaveProperty('acknowledged_at')
  })

  it('resolve: open -> resolved, with at and no name (the actor comes from the token)', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.resolveAlert(target.alert_id))
    const after = find(result, target.alert_id)
    expect(after).toEqual({
      ...target,
      status: 'resolved',
      resolved_at: ACTION_TIME.toISOString(),
    })
    expect(after).not.toHaveProperty('resolved_by')
  })

  it('resolveAlert takes only the alert ID', async () => {
    const { result } = await loaded()
    expect(result.current.resolveAlert).toHaveLength(1)
  })

  it('resolve leaves a device problem unchanged (only the watchdog resolves those)', async () => {
    const { result, data } = await loaded()
    for (const target of data.alerts.filter((a) => ['device_offline', 'battery_low', 'not_worn'].includes(a.alert_type))) {
      act(() => result.current.resolveAlert(target.alert_id))
      expect(find(result, target.alert_id)).toBe(target)
    }
  })

  it('resolve: acknowledged -> resolved, keeping the acknowledgement and its note', async () => {
    const { result, data } = await loaded()
    const acked = firstWith(data, 'acknowledged')
    expect(acked.ack_note).toBeDefined()
    act(() => result.current.resolveAlert(acked.alert_id))
    expect(find(result, acked.alert_id)).toMatchObject({
      status: 'resolved',
      ack_note: acked.ack_note,
      acknowledged_at: acked.acknowledged_at,
      resolved_at: ACTION_TIME.toISOString(),
    })
  })

  it('resolving twice keeps the first resolve time', async () => {
    const { result, data } = await loaded()
    const target = data.alerts[0]
    act(() => result.current.resolveAlert(target.alert_id))
    const first = find(result, target.alert_id)
    vi.setSystemTime(new Date(ACTION_TIME.getTime() + 60_000))
    act(() => result.current.resolveAlert(target.alert_id))
    expect(find(result, target.alert_id)).toBe(first)
    expect(first.resolved_at).toBe(ACTION_TIME.toISOString())
  })

  describe('a cancelled alert ("I\'m OK")', () => {
    async function loadedWithCancelled() {
      const data = demoDataFor('staff', NOW)
      const target = firstWith(data, 'open')
      const cancelled: ResidentAlert = {
        ...target,
        status: 'cancelled',
        cancelled_at: NOW.toISOString(),
        cancelled_by_role: 'resident',
      }
      const withCancelled = { ...data, alerts: data.alerts.map((a) => (a === target ? cancelled : a)) }
      const loader = vi.fn<DashboardLoader>().mockResolvedValue(withCancelled)
      const hook = renderHook(() => useDashboardData('staff', loader))
      await waitFor(() => expect(hook.result.current.data).not.toBeNull())
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(ACTION_TIME)
      return { ...hook, data: withCancelled, cancelled }
    }

    it('resolve is a no-op: same object, still cancelled, no resolved_by/at', async () => {
      const { result, cancelled } = await loadedWithCancelled()
      act(() => result.current.resolveAlert(cancelled.alert_id))
      const after = find(result, cancelled.alert_id)
      expect(after).toBe(cancelled)
      expect(after.status).toBe('cancelled')
      expect(after).not.toHaveProperty('resolved_by')
      expect(after).not.toHaveProperty('resolved_at')
    })

    it('acknowledge is a no-op too', async () => {
      const { result, cancelled } = await loadedWithCancelled()
      act(() => result.current.acknowledgeAlert(cancelled.alert_id))
      const after = find(result, cancelled.alert_id)
      expect(after).toBe(cancelled)
      expect(after).not.toHaveProperty('acknowledged_at')
    })

    it('resolving it leaves every other alert untouched (same objects)', async () => {
      const { result, data, cancelled } = await loadedWithCancelled()
      act(() => result.current.resolveAlert(cancelled.alert_id))
      alertsOf(result).forEach((a, i) => expect(a).toBe(data.alerts[i]))
    })
  })

  it('resolve leaves every other alert untouched', async () => {
    const { result, data } = await loaded()
    const target = data.alerts[1]
    act(() => result.current.resolveAlert(target.alert_id))
    expect(alertsOf(result)).toHaveLength(data.alerts.length)
    alertsOf(result).forEach((a, i) => {
      if (a.alert_id !== target.alert_id) expect(a).toBe(data.alerts[i])
    })
  })

  it('an unknown alert ID changes nothing', async () => {
    const { result, data } = await loaded()
    act(() => result.current.acknowledgeAlert('alert-test-missing'))
    act(() => result.current.resolveAlert('alert-test-missing'))
    alertsOf(result).forEach((a, i) => expect(a).toBe(data.alerts[i]))
  })

  it('is a no-op before data loads', async () => {
    const pending = deferred<DashboardData>()
    const loader = vi.fn<DashboardLoader>(() => pending.promise)
    const { result } = renderHook(() => useDashboardData('staff', loader))
    const data = demoDataFor('staff', NOW)
    act(() => result.current.acknowledgeAlert(data.alerts[0].alert_id))
    act(() => result.current.resolveAlert(data.alerts[0].alert_id))
    expect(result.current).toMatchObject({ data: null, loading: true, error: false })

    await act(async () => pending.resolve(data))
    expect(result.current.data).toBe(data)
  })
})
