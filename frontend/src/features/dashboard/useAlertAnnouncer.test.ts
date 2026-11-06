import { act, renderHook } from '@testing-library/react'
import { demoDataFor } from '../../test/dashboardMock'
import type { Resident, ResidentAlert } from '../../types/dashboard'
import { describeAlert, useAlertAnnouncer } from './useAlertAnnouncer'

const NOW = new Date('2026-10-18T02:30:00Z')
const data = demoDataFor('staff', NOW)
const residentsById = new Map<string, Resident>(data.residents.map((r) => [r.resident_id, r]))

function alert(id: string, overrides: Partial<ResidentAlert> = {}): ResidentAlert {
  return {
    alert_id: id,
    resident_id: 'res-0003',
    device_id: 'wearable-test-003',
    alert_type: 'fall',
    severity: 'critical',
    status: 'open',
    event_ts: NOW.toISOString(),
    ...overrides,
  }
}

/** The message without the alternating trailing no-break space. */
const msg = (result: { current: { message: string } }) => result.current.message.replace(/\u00a0$/, '')

function setup(initial: ResidentAlert[]) {
  return renderHook(({ alerts }) => useAlertAnnouncer(alerts, residentsById), {
    initialProps: { alerts: initial },
  })
}

describe('describeAlert', () => {
  it('reads "<type label>, <resident name>"', () => {
    expect(describeAlert(alert('a1'), residentsById)).toBe('Fall detected, Test Resident C')
    expect(describeAlert(alert('a2', { alert_type: 'low_battery', resident_id: 'res-0013' }), residentsById)).toBe(
      'Low battery, Test Resident M',
    )
  })

  it('falls back to "Unknown resident"', () => {
    expect(describeAlert(alert('a1', { resident_id: 'resident-test-999' }), residentsById)).toBe(
      'Fall detected, Unknown resident',
    )
  })
})

describe('useAlertAnnouncer', () => {
  it('is quiet on first load, however many alerts there are', () => {
    const { result } = setup(data.alerts)
    expect(msg(result)).toBe('')
  })

  it('announces a single new critical alert', () => {
    const { result, rerender } = setup(data.alerts)
    rerender({ alerts: [alert('alert-test-new'), ...data.alerts] })
    expect(msg(result)).toBe('New critical alert: Fall detected, Test Resident C.')
  })

  it('announces a single new non-critical alert', () => {
    const { result, rerender } = setup(data.alerts)
    const fresh = alert('alert-test-new', { alert_type: 'inactivity', severity: 'warning', resident_id: 'res-0002' })
    rerender({ alerts: [fresh, ...data.alerts] })
    expect(msg(result)).toBe('New alert: Inactivity, Test Resident B.')
  })

  it('summarises several new alerts with the critical count', () => {
    const { result, rerender } = setup([])
    rerender({
      alerts: [
        alert('n1'),
        alert('n2', { severity: 'warning', alert_type: 'inactivity' }),
        alert('n3', { severity: 'warning', alert_type: 'low_battery' }),
      ],
    })
    expect(msg(result)).toBe('3 new alerts, 1 critical.')
  })

  it('says "0 critical" when none of the new alerts are critical', () => {
    const { result, rerender } = setup([])
    rerender({ alerts: [alert('n1', { severity: 'warning', alert_type: 'spo2_low' }), alert('n2', { severity: 'warning' })] })
    expect(msg(result)).toBe('2 new alerts, 0 critical.')
  })

  it('ignores new alerts that arrive already resolved', () => {
    const { result, rerender } = setup(data.alerts)
    rerender({ alerts: [alert('n1', { status: 'resolved' }), ...data.alerts] })
    expect(msg(result)).toBe('')
  })

  it('ignores new alerts that arrive already cancelled ("I\'m OK")', () => {
    const { result, rerender } = setup(data.alerts)
    const cancelled = alert('n1', { status: 'cancelled', cancelled_at: NOW.toISOString(), cancelled_by_role: 'resident' })
    rerender({ alerts: [cancelled, ...data.alerts] })
    expect(msg(result)).toBe('')
  })

  it('announces a new acknowledged alert (it is on the open list)', () => {
    const { result, rerender } = setup([])
    rerender({
      alerts: [alert('n1', { status: 'acknowledged', acknowledged_at: NOW.toISOString() })],
    })
    expect(msg(result)).toBe('New critical alert: Fall detected, Test Resident C.')
  })

  it('counts only the open-list alerts among several new ones', () => {
    const { result, rerender } = setup([])
    rerender({
      alerts: [
        alert('n1', { status: 'cancelled', cancelled_at: NOW.toISOString(), cancelled_by_role: 'resident' }),
        alert('n2', { severity: 'warning', alert_type: 'inactivity', resident_id: 'res-0002' }),
        alert('n3', { status: 'resolved', resolved_by: 'Test Nurse A', resolved_at: NOW.toISOString() }),
      ],
    })
    // One open alert left: the single-alert message, not "3 new alerts".
    expect(msg(result)).toBe('New alert: Inactivity, Test Resident B.')
  })

  it('does not announce a seen alert that is later cancelled', () => {
    const { result, rerender } = setup(data.alerts)
    rerender({
      alerts: data.alerts.map((a, i) =>
        i === 0 ? { ...a, status: 'cancelled' as const, cancelled_at: NOW.toISOString(), cancelled_by_role: 'resident' as const } : a,
      ),
    })
    expect(msg(result)).toBe('')
  })

  it('does not announce status changes to alerts it has seen', () => {
    const { result, rerender } = setup(data.alerts)
    rerender({
      alerts: data.alerts.map((a, i) =>
        i === 0 ? { ...a, status: 'acknowledged' as const, acknowledged_at: NOW.toISOString() } : a,
      ),
    })
    expect(msg(result)).toBe('')
  })

  it('compares with the previous render, so the same alert is announced only once', () => {
    const { result, rerender } = setup([])
    const first = [alert('n1')]
    rerender({ alerts: first })
    expect(msg(result)).toBe('New critical alert: Fall detected, Test Resident C.')
    act(() => result.current.announce('Acknowledged: Fall detected, Test Resident C.'))
    rerender({ alerts: [...first] }) // new array, same alerts
    expect(msg(result)).toBe('Acknowledged: Fall detected, Test Resident C.')
  })

  it('announce() sets the message directly', () => {
    const { result } = setup(data.alerts)
    act(() => result.current.announce('Escalated to the care centre nurse: Low blood oxygen, Test Resident H.'))
    expect(msg(result)).toBe('Escalated to the care centre nurse: Low blood oxygen, Test Resident H.')
  })

  it('announcing the same text twice still changes the message, so it is read again', () => {
    const { result } = setup(data.alerts)
    const text = 'Resolved: Fall detected, Test Resident C.'
    act(() => result.current.announce(text))
    const first = result.current.message
    act(() => result.current.announce(text))
    const second = result.current.message
    expect(second).not.toBe(first)
    expect([first, second].map((m) => m.replace(/\u00a0$/, ''))).toEqual([text, text])
    // The only difference is a trailing no-break space, which screen readers do not read.
    expect([first, second].sort((a, b) => a.length - b.length)).toEqual([text, `${text}\u00a0`])
    act(() => result.current.announce(text))
    expect(result.current.message).not.toBe(second)
  })

  it('a new-alert announcement after an identical action announcement still changes the message', () => {
    const { result, rerender } = setup([])
    act(() => result.current.announce('New critical alert: Fall detected, Test Resident C.'))
    const before = result.current.message
    rerender({ alerts: [alert('n1')] })
    expect(result.current.message).not.toBe(before)
    expect(msg(result)).toBe('New critical alert: Fall detected, Test Resident C.')
  })
})
