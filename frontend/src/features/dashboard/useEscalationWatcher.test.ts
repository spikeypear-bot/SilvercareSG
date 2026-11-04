import { act, renderHook } from '@testing-library/react'
import type { ResidentAlert } from '../../types/dashboard'
import { useEscalationWatcher } from './useEscalationWatcher'

const NOW = new Date('2026-10-18T02:30:00Z')
const inMs = (ms: number) => new Date(NOW.getTime() + ms).toISOString()

function alert(id: string, overrides: Partial<ResidentAlert> = {}): ResidentAlert {
  return {
    alert_id: id,
    resident_id: 'res-0008',
    device_id: 'bedroom-test-008',
    alert_type: 'possible_fall',
    severity: 'warning',
    status: 'open',
    event_ts: NOW.toISOString(),
    ...overrides,
  }
}

function watch(initial: ResidentAlert[]) {
  const onEscalated = vi.fn<(alert: ResidentAlert) => void>()
  const hook = renderHook(({ alerts }) => useEscalationWatcher(alerts, onEscalated), {
    initialProps: { alerts: initial },
  })
  return { onEscalated, ...hook }
}

const tick = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms)
  })

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useEscalationWatcher', () => {
  it('fires once, at escalates_at, with the alert', () => {
    const a = alert('alert-test-001', { escalates_at: inMs(90_000) })
    const { onEscalated } = watch([a])
    tick(89_999)
    expect(onEscalated).not.toHaveBeenCalled()
    tick(1)
    expect(onEscalated).toHaveBeenCalledTimes(1)
    expect(onEscalated).toHaveBeenCalledWith(a)
    tick(10 * 60_000)
    expect(onEscalated).toHaveBeenCalledTimes(1)
  })

  it('fires separately for each alert in its window', () => {
    const a = alert('alert-test-001', { escalates_at: inMs(10_000) })
    const b = alert('alert-test-002', { escalates_at: inMs(20_000) })
    const { onEscalated } = watch([a, b])
    tick(10_000)
    expect(onEscalated.mock.calls).toEqual([[a]])
    tick(10_000)
    expect(onEscalated.mock.calls).toEqual([[a], [b]])
  })

  it.each([
    ['already past', alert('alert-test-001', { escalates_at: inMs(-1_000) })],
    ['exactly now', alert('alert-test-001', { escalates_at: inMs(0) })],
    ['acknowledged', alert('alert-test-001', { status: 'acknowledged', escalates_at: inMs(5_000) })],
    ['resolved', alert('alert-test-001', { status: 'resolved', escalates_at: inMs(5_000) })],
    ['cancelled', alert('alert-test-001', { status: 'cancelled', escalates_at: inMs(5_000) })],
    ['without escalates_at', alert('alert-test-001')],
  ])('never fires for an alert %s', (_, a) => {
    const { onEscalated } = watch([a])
    tick(10 * 60_000)
    expect(onEscalated).not.toHaveBeenCalled()
  })

  it('is cleared when the alert is acknowledged before the deadline', () => {
    const a = alert('alert-test-001', { escalates_at: inMs(90_000) })
    const { onEscalated, rerender } = watch([a])
    tick(30_000)
    rerender({ alerts: [{ ...a, status: 'acknowledged', acknowledged_at: inMs(30_000) }] })
    tick(10 * 60_000)
    expect(onEscalated).not.toHaveBeenCalled()
  })

  it('is cleared when the alert is resolved before the deadline', () => {
    const a = alert('alert-test-001', { escalates_at: inMs(90_000) })
    const { onEscalated, rerender } = watch([a])
    tick(30_000)
    rerender({ alerts: [{ ...a, status: 'resolved', resolved_by: 'Test Nurse A', resolved_at: inMs(30_000) }] })
    tick(10 * 60_000)
    expect(onEscalated).not.toHaveBeenCalled()
  })

  it('a change to another alert keeps the original deadline and fires only once', () => {
    const a = alert('alert-test-001', { escalates_at: inMs(90_000) })
    const other = alert('alert-test-002')
    const { onEscalated, rerender } = watch([a, other])
    tick(30_000)
    rerender({ alerts: [a, { ...other, status: 'acknowledged' }] })
    tick(59_999)
    expect(onEscalated).not.toHaveBeenCalled()
    tick(1)
    expect(onEscalated).toHaveBeenCalledTimes(1)
    // Another change after the deadline must not fire it again.
    rerender({ alerts: [a, { ...other, status: 'resolved' }] })
    tick(10 * 60_000)
    expect(onEscalated).toHaveBeenCalledTimes(1)
  })

  it('is cleared on unmount', () => {
    const a = alert('alert-test-001', { escalates_at: inMs(90_000) })
    const { onEscalated, unmount } = watch([a])
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    unmount()
    expect(vi.getTimerCount()).toBe(0)
    tick(10 * 60_000)
    expect(onEscalated).not.toHaveBeenCalled()
  })

  it('calls the latest onEscalated, not the one from the first render', () => {
    const a = alert('alert-test-001', { escalates_at: inMs(5_000) })
    const first = vi.fn<(alert: ResidentAlert) => void>()
    const second = vi.fn<(alert: ResidentAlert) => void>()
    const alerts = [a]
    const { rerender } = renderHook(({ cb }) => useEscalationWatcher(alerts, cb), {
      initialProps: { cb: first },
    })
    rerender({ cb: second })
    tick(5_000)
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledWith(a)
  })
})
