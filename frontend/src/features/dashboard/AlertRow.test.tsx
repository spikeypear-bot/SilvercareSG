import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { demoDataFor } from '../../test/dashboardMock'
import type { ResidentAlert } from '../../types/dashboard'
import { AcknowledgedLines, AlertRow, RoutingLine } from './AlertRow'
import { sentToNurseAtOnce } from './stats'
import { ALERT_TYPE_LABELS } from './labels'

const NOW = new Date('2026-10-18T02:30:00Z')

function baseAlert(overrides: Partial<ResidentAlert> = {}): ResidentAlert {
  return {
    alert_id: 'alert-test-001',
    resident_id: 'res-0008',
    device_id: 'wearable-test-008',
    alert_type: 'spo2_low',
    severity: 'warning',
    status: 'open',
    event_ts: new Date(NOW.getTime() - 30_000).toISOString(),
    ...overrides,
  }
}

function renderRow(props: Partial<Parameters<typeof AlertRow>[0]> = {}) {
  const data = demoDataFor('staff', NOW)
  const resident = data.residents.find((r) => r.resident_id === 'res-0008')
  return render(
    <ul>
      <AlertRow alert={baseAlert()} resident={resident} now={NOW} {...props} />
    </ul>,
  )
}

describe('AlertRow', () => {
  it('shows the resident line with name, ID, and home ID', () => {
    renderRow()
    const row = screen.getByRole('listitem')
    expect(row).toHaveTextContent('Test Resident H')
    expect(row).toHaveTextContent('res-0008')
    expect(row).toHaveTextContent(/Home\s*home-\d{4}/)
  })

  it('falls back to "Unknown resident" with no home ID', () => {
    render(
      <ul>
        <AlertRow alert={baseAlert({ resident_id: 'resident-test-999' })} resident={undefined} now={NOW} />
      </ul>,
    )
    const row = screen.getByRole('listitem')
    expect(row).toHaveTextContent('Unknown resident')
    expect(row).toHaveTextContent('resident-test-999')
    expect(row).not.toHaveTextContent(/home-/)
  })

  it('has no buttons without actions', () => {
    renderRow()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('action buttons call their handlers', async () => {
    const user = userEvent.setup()
    const onAcknowledge = vi.fn()
    const onResolve = vi.fn()
    renderRow({ actions: { onAcknowledge, onResolve } })
    await user.click(screen.getByRole('button', { name: 'Acknowledge: Low blood oxygen, Test Resident H' }))
    // Acknowledge only opens the note form; submitting it (same name) acknowledges.
    expect(onAcknowledge).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Acknowledge: Low blood oxygen, Test Resident H' }))
    expect(onAcknowledge).toHaveBeenCalledTimes(1)
    expect(onAcknowledge).toHaveBeenCalledWith(undefined)
    await user.click(screen.getByRole('button', { name: 'Resolve: Low blood oxygen, Test Resident H' }))
    expect(onResolve).toHaveBeenCalledTimes(1)
  })

  it('shows nothing about acknowledgement when acknowledged_at is missing', () => {
    renderRow({ alert: baseAlert({ status: 'acknowledged' }) })
    const row = screen.getByRole('listitem')
    expect(within(row).getByText('Acknowledged')).toBeInTheDocument()
    expect(row).not.toHaveTextContent('Acknowledged by')
  })
})

describe('AlertRow alert types', () => {
  it.each([
    ['fall', 'Fall detected'],
    ['hr_high', 'High heart rate'],
    ['hr_low', 'Low heart rate'],
    ['spo2_low', 'Low blood oxygen'],
    ['skin_temp_high', 'High skin temperature'],
    ['skin_temp_low', 'Low skin temperature'],
    ['possible_fall', 'Possible fall'],
    ['not_worn', 'Wearable not worn'],
    ['device_offline', 'Device offline'],
    ['battery_low', 'Low battery'],
  ] as const)('%s shows "%s" and names the buttons with it', (type, label) => {
    // A possible fall is always critical (docs/features/possible-fall.md), never a warning.
    const severity = type === 'possible_fall' ? 'critical' : 'warning'
    renderRow({
      alert: baseAlert({ alert_type: type, severity }),
      actions: { onAcknowledge: vi.fn(), onResolve: vi.fn() },
    })
    const row = screen.getByRole('listitem')
    expect(within(row).getByText(label)).toBeInTheDocument()
    expect(row).not.toHaveTextContent('Vitals need attention')
    if (['not_worn', 'device_offline', 'battery_low'].includes(type)) {
      // Device problems: no nurse buttons (acknowledged by tech staff or caregivers, resolved by the watchdog).
      expect(screen.queryAllByRole('button')).toEqual([])
    } else {
      expect(screen.getByRole('button', { name: `Resolve: ${label}, Test Resident H` })).toBeInTheDocument()
    }
  })

  it('every alert type has a label', () => {
    Object.values(ALERT_TYPE_LABELS).forEach((label) => expect(label.trim()).not.toBe(''))
    expect(Object.keys(ALERT_TYPE_LABELS)).toHaveLength(11)
  })
})

describe('AlertRow cancelled (the wearable "I am OK" cancel)', () => {
  const actions = () => ({ onAcknowledge: vi.fn(), onResolve: vi.fn() })

  it('shows a "Cancelled" lifecycle label with its own icon', () => {
    const { unmount } = renderRow({ alert: baseAlert({ status: 'cancelled', alert_type: 'fall', severity: 'critical' }) })
    const label = screen.getByText('Cancelled')
    const icon = label.querySelector('svg[aria-hidden="true"]')
    expect(icon).not.toBeNull()
    expect(screen.queryByText('Open')).not.toBeInTheDocument()
    const cancelledIcon = icon?.innerHTML
    unmount()

    const otherIcons = (['open', 'acknowledged', 'resolved'] as const).map((status) => {
      const view = renderRow({ alert: baseAlert({ status }) })
      const markup = view.container.querySelector('li span:not([data-tone]) svg')?.innerHTML
      view.unmount()
      return markup
    })
    expect(cancelledIcon).toBeTruthy()
    otherIcons.forEach((markup) => {
      expect(markup).toBeTruthy()
      expect(markup).not.toBe(cancelledIcon)
    })
  })

  it('has no Acknowledge and no Resolve, even when actions are passed', () => {
    const handlers = actions()
    renderRow({ alert: baseAlert({ status: 'cancelled', alert_type: 'fall', severity: 'critical' }), actions: handlers })
    expect(screen.queryAllByRole('button')).toEqual([])
    expect(handlers.onResolve).not.toHaveBeenCalled()
    expect(handlers.onAcknowledge).not.toHaveBeenCalled()
  })

  it.each(['cancelled', 'resolved'] as const)(
    'a %s alert has no buttons even when actions are passed',
    (status) => {
      renderRow({
        alert: baseAlert({
          status,
          resolved_by: status === 'resolved' ? 'Test Nurse A' : undefined,
          resolved_at: status === 'resolved' ? NOW.toISOString() : undefined,
        }),
        actions: actions(),
      })
      expect(screen.queryAllByRole('button')).toEqual([])
    },
  )

  it('only open alerts have Acknowledge; only open and acknowledged alerts have Resolve', () => {
    for (const status of ['open', 'acknowledged', 'cancelled', 'resolved'] as const) {
      const view = renderRow({ alert: baseAlert({ status }), actions: actions() })
      const ack = screen.queryAllByRole('button', { name: /^Acknowledge/ })
      expect(ack, status).toHaveLength(status === 'open' ? 1 : 0)
      const resolve = screen.queryAllByRole('button', { name: /^Resolve/ })
      expect(resolve, status).toHaveLength(status === 'open' || status === 'acknowledged' ? 1 : 0)
      view.unmount()
    }
  })

  it('shows "Cancelled by the resident ("I\'m OK")" with the time from cancelled_at', () => {
    const cancelledAt = new Date(NOW.getTime() - 12 * 60_000).toISOString()
    renderRow({
      alert: baseAlert({ status: 'cancelled', cancelled_at: cancelledAt, cancelled_by_role: 'resident' }),
    })
    const line = screen.getByText(/^Cancelled by the resident/)
    expect(line.tagName).toBe('P')
    expect(line).toHaveTextContent(/^Cancelled by the resident \("I'm OK"\), 12 min ago/)
    expect(line).toHaveClass('text-base')
    const time = line.querySelector('time')
    expect(time).toHaveAttribute('datetime', cancelledAt)
    // The time is from cancelled_at, not event_ts (30 s ago).
    expect(time).not.toHaveAttribute('datetime', baseAlert().event_ts)
  })

  it('a cancel 30 s after the alert reads "just now" at NOW', () => {
    const cancelledAt = new Date(NOW.getTime() - 10_000).toISOString()
    renderRow({ alert: baseAlert({ status: 'cancelled', cancelled_at: cancelledAt, cancelled_by_role: 'resident' }) })
    expect(screen.getByText(/^Cancelled by the resident/)).toHaveTextContent(/^Cancelled by the resident \("I'm OK"\), just now/)
  })

  it('shows no cancelled line without cancelled_at', () => {
    renderRow({ alert: baseAlert({ status: 'cancelled' }) })
    expect(screen.getByRole('listitem')).not.toHaveTextContent('Cancelled by')
    expect(screen.getByText('Cancelled')).toBeInTheDocument()
  })

  it.each(['open', 'acknowledged', 'resolved'] as const)(
    'a %s alert shows no cancelled line, even with a stray cancelled_at',
    (status) => {
      renderRow({ alert: baseAlert({ status, cancelled_at: NOW.toISOString(), cancelled_by_role: 'resident' }) })
      expect(screen.getByRole('listitem')).not.toHaveTextContent('Cancelled by')
    },
  )

  it('shows no acknowledged or resolved line on a cancelled alert', () => {
    renderRow({ alert: baseAlert({ status: 'cancelled', cancelled_at: NOW.toISOString(), cancelled_by_role: 'resident' }) })
    const row = screen.getByRole('listitem')
    expect(row).not.toHaveTextContent('Acknowledged by')
    expect(row).not.toHaveTextContent('Resolved by')
  })
})

describe('AlertRow escalated_at (real clock)', () => {
  const past = () => new Date(Date.now() - 30_000).toISOString()
  const future = () => new Date(Date.now() + 60_000).toISOString()

  it('shows "Escalated to the care centre nurse" from escalated_at alone (no escalates_at)', () => {
    renderRow({ alert: baseAlert({ escalated_at: past() }) })
    const row = screen.getByRole('listitem')
    expect(within(row).getByText('Escalated to the care centre nurse')).toBeInTheDocument()
    expect(within(row).getByText('Escalated to the care centre nurse').querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
  })

  it('escalated_at wins over a future escalates_at: no countdown', () => {
    renderRow({ alert: baseAlert({ escalated_at: past(), escalates_at: future() }) })
    expect(screen.getAllByText('Escalated to the care centre nurse')).toHaveLength(1)
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
  })

  it.each(['acknowledged', 'cancelled', 'resolved'] as const)(
    'a %s alert shows no escalation line, whatever escalation fields it has',
    (status) => {
      const variants: Array<Partial<ResidentAlert>> = [
        { escalated_at: past() },
        { escalates_at: future() },
        { escalates_at: past() },
        { escalated_at: past(), escalates_at: future() },
      ]
      variants.forEach((fields) => {
        const view = renderRow({
          alert: baseAlert({
            status,
            ...fields,
            ...(status === 'cancelled' && { cancelled_at: past(), cancelled_by_role: 'resident' as const }),
          }),
        })
        const row = screen.getByRole('listitem')
        expect(row, JSON.stringify(fields)).not.toHaveTextContent(/Escalat/)
        expect(screen.queryByRole('timer')).not.toBeInTheDocument()
        view.unmount()
      })
    },
  )

  it('an open alert with neither field shows no escalation line', () => {
    renderRow({ alert: baseAlert() })
    expect(screen.getByRole('listitem')).not.toHaveTextContent(/Escalat/)
  })
})

describe('AlertRow escalation countdown (fake clock)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  const tick = (ms: number) =>
    act(() => {
      vi.advanceTimersByTime(ms)
    })

  it('rounds partial seconds up and flips to "Escalated to the care centre nurse" at zero', () => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(NOW.getTime() + 2_500).toISOString() }) })
    expect(screen.getByRole('timer')).toHaveTextContent(/^Escalates to the care centre nurse in 0:03$/)
    tick(1000)
    expect(screen.getByRole('timer')).toHaveTextContent(/^Escalates to the care centre nurse in 0:02$/)
    tick(1000)
    expect(screen.getByRole('timer')).toHaveTextContent(/^Escalates to the care centre nurse in 0:01$/)
    tick(1000)
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.getByText('Escalated to the care centre nurse')).toBeInTheDocument()
    tick(10_000)
    expect(screen.getByText('Escalated to the care centre nurse')).toBeInTheDocument()
  })

  it('shows "Escalated to the care centre nurse" straight away for an alert already past its deadline', () => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(NOW.getTime() - 1_000).toISOString() }) })
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.getByText('Escalated to the care centre nurse')).toBeInTheDocument()
  })

  it('an escalates_at exactly now counts as escalated', () => {
    renderRow({ alert: baseAlert({ escalates_at: NOW.toISOString() }) })
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.getByText('Escalated to the care centre nurse')).toBeInTheDocument()
  })

  it('starts at the API escalates_at, e.g. 4:30 left for a warning raised 30 s ago', () => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(Date.parse(baseAlert().event_ts) + 300_000).toISOString() }) })
    expect(screen.getByRole('timer')).toHaveTextContent(/^Escalates to the care centre nurse in 4:30$/)
    tick(269_000)
    expect(screen.getByRole('timer')).toHaveTextContent(/^Escalates to the care centre nurse in 0:01$/)
    tick(1000)
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.getByText('Escalated to the care centre nurse')).toBeInTheDocument()
  })

  it.each([
    [275, '4:35'],
    [300, '5:00'],
    [65, '1:05'],
    [60, '1:00'],
    [59, '0:59'],
    [10, '0:10'],
  ])('formats %i s left as m:ss ("%s")', (seconds, text) => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(NOW.getTime() + seconds * 1000).toISOString() }) })
    expect(screen.getByRole('timer')).toHaveTextContent(new RegExp(`^Escalates to the care centre nurse in ${text}$`))
  })

  it('ticks from 1:00 to 0:59 across the minute boundary', () => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(NOW.getTime() + 61_000).toISOString() }) })
    expect(screen.getByRole('timer')).toHaveTextContent(/in 1:01$/)
    tick(1000)
    expect(screen.getByRole('timer')).toHaveTextContent(/in 1:00$/)
    tick(1000)
    expect(screen.getByRole('timer')).toHaveTextContent(/in 0:59$/)
  })

  it('the countdown is a timer, not a live region', () => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(NOW.getTime() + 275_000).toISOString() }) })
    const timer = screen.getByRole('timer')
    expect(timer.closest('[aria-live]')).toBeNull()
    expect(timer.parentElement?.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it('a countdown that runs out on a critical alert still reads "Escalated to the care centre nurse"', () => {
    // The countdown never says "at once": that needs escalated_at from the API.
    renderRow({ alert: baseAlert({ severity: 'critical', escalates_at: new Date(NOW.getTime() + 1_000).toISOString() }) })
    tick(1000)
    expect(screen.getByText('Escalated to the care centre nurse')).toBeInTheDocument()
    expect(screen.queryByText('Sent to the care centre nurse at once')).not.toBeInTheDocument()
  })

  it('shows neither for acknowledged or resolved alerts', () => {
    const escalates_at = new Date(NOW.getTime() + 60_000).toISOString()
    const { rerender } = renderRow({ alert: baseAlert({ status: 'acknowledged', escalates_at }) })
    expect(screen.getByRole('listitem')).not.toHaveTextContent(/Escalat/)
    rerender(
      <ul>
        <AlertRow alert={baseAlert({ status: 'resolved', escalates_at })} resident={undefined} now={NOW} />
      </ul>,
    )
    expect(screen.getByRole('listitem')).not.toHaveTextContent(/Escalat/)
  })

  it('countdown, escalation, and acknowledgement text use the 16px base size with 20px icons', () => {
    const { rerender } = renderRow({
      alert: baseAlert({ escalates_at: new Date(NOW.getTime() + 60_000).toISOString() }),
    })
    const countdown = screen.getByRole('timer').parentElement as HTMLElement
    expect(countdown).toHaveClass('text-base')
    expect(countdown.querySelector('svg')).toHaveClass('size-5')

    rerender(
      <ul>
        <AlertRow alert={baseAlert({ escalates_at: NOW.toISOString() })} resident={undefined} now={NOW} />
      </ul>,
    )
    const escalated = screen.getByText('Escalated to the care centre nurse')
    expect(escalated).toHaveClass('text-base')
    expect(escalated.querySelector('svg')).toHaveClass('size-5')

    rerender(
      <ul>
        <AlertRow
          alert={baseAlert({ status: 'acknowledged', acknowledged_at: NOW.toISOString() })}
          resident={undefined}
          now={NOW}
        />
      </ul>,
    )
    expect(screen.getByText(/^Acknowledged by the care centre nurse,/)).toHaveClass('text-base')
  })
})

describe('AlertRow acknowledge (no actor name)', () => {
  it('shows "Acknowledged by the care centre nurse" with the time from acknowledged_at', () => {
    const acknowledgedAt = new Date(NOW.getTime() - 6 * 60_000).toISOString()
    renderRow({ alert: baseAlert({ status: 'acknowledged', acknowledged_at: acknowledgedAt }) })
    const line = screen.getByText(/^Acknowledged by the care centre nurse/)
    expect(line.tagName).toBe('P')
    expect(line).toHaveTextContent(/^Acknowledged by the care centre nurse, 6 min ago \(/)
    expect(line.querySelector('time')).toHaveAttribute('datetime', acknowledgedAt)
  })

  it.each(['open', 'acknowledged', 'cancelled', 'resolved'] as const)(
    'a %s alert with acknowledged_at shows the acknowledge line',
    (status) => {
      renderRow({
        alert: baseAlert({
          status,
          acknowledged_at: new Date(NOW.getTime() - 60_000).toISOString(),
          ...(status === 'resolved' && { resolved_by: 'Test Nurse A', resolved_at: NOW.toISOString() }),
        }),
      })
      expect(screen.getByRole('listitem')).toHaveTextContent(/Acknowledged by the care centre nurse, 1 min ago/)
    },
  )

  it('never shows a staff name in the acknowledge line', () => {
    renderRow({ alert: baseAlert({ status: 'acknowledged', acknowledged_at: NOW.toISOString() }) })
    const line = screen.getByText(/^Acknowledged by/)
    expect(line).not.toHaveTextContent(/Test Nurse/)
  })
})

describe('AlertRow device problems (user decision 2026-11-05: nurses acknowledge health alerts only)', () => {
  const actions = () => ({ onAcknowledge: vi.fn(), onResolve: vi.fn() })

  it.each([
    ['device_offline', 'Device offline'],
    ['battery_low', 'Low battery'],
    ['not_worn', 'Wearable not worn'],
  ] as const)('an open or acknowledged %s gets no Acknowledge and no Resolve', (alert_type, label) => {
    renderRow({ alert: baseAlert({ alert_type }), actions: actions() })
    expect(within(screen.getByRole('listitem')).getByText(label)).toBeInTheDocument()
    expect(screen.queryAllByRole('button')).toEqual([])
    cleanup()
    renderRow({
      alert: baseAlert({ alert_type, status: 'acknowledged', acknowledged_at: NOW.toISOString(), acknowledged_by_role: 'caregiver', acknowledged_by: 'Test Caregiver A' }),
      actions: actions(),
    })
    expect(screen.queryAllByRole('button')).toEqual([])
  })

  it('an ops acknowledge reads "Acknowledged by the ops team", never the care centre nurse', () => {
    const acknowledgedAt = new Date(NOW.getTime() - 5 * 60_000).toISOString()
    renderRow({
      alert: baseAlert({ alert_type: 'device_offline', status: 'acknowledged', acknowledged_at: acknowledgedAt, acknowledged_by_role: 'ops' }),
    })
    const line = screen.getByText(/^Acknowledged by/)
    expect(line).toHaveTextContent(/^Acknowledged by the ops team, 5 min ago/)
    expect(line.querySelector('time')).toHaveAttribute('datetime', acknowledgedAt)
    expect(screen.getByRole('listitem')).not.toHaveTextContent(/care centre nurse|Message from the care centre/)
  })

  it('an acknowledge role this build does not know reads "Acknowledged, <time>", never the care centre nurse', () => {
    // Simulates a future API value: the type system doesn't allow it, the runtime must still cope.
    const alert = { ...baseAlert({ status: 'acknowledged', acknowledged_at: NOW.toISOString() }), acknowledged_by_role: 'tech' } as unknown as ResidentAlert
    renderRow({ alert })
    expect(screen.getByText(/^Acknowledged,/)).toHaveTextContent(/^Acknowledged, just now/)
    expect(screen.getByRole('listitem')).not.toHaveTextContent(/care centre nurse|tech/)
  })

  it('a resolved alert without a resolver name reads "Resolved, <time>" (a watchdog resolve, or a resolve made on this page)', () => {
    renderRow({ alert: baseAlert({ alert_type: 'battery_low', status: 'resolved', resolved_at: NOW.toISOString() }) })
    expect(screen.getByText(/^Resolved,/)).toHaveTextContent(/^Resolved, just now/)
    expect(screen.getByRole('listitem')).not.toHaveTextContent('Resolved by')
  })

  it('an open health alert still gets Acknowledge and Resolve', () => {
    renderRow({ alert: baseAlert({ alert_type: 'spo2_low' }), actions: actions() })
    expect(screen.getByRole('button', { name: 'Acknowledge: Low blood oxygen, Test Resident H' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resolve: Low blood oxygen, Test Resident H' })).toBeInTheDocument()
  })

  it('a caregiver acknowledge reads "Acknowledged by <name> (caregiver)", never the care centre nurse', () => {
    const acknowledgedAt = new Date(NOW.getTime() - 3 * 60_000).toISOString()
    renderRow({
      alert: baseAlert({
        alert_type: 'battery_low',
        status: 'acknowledged',
        acknowledged_at: acknowledgedAt,
        acknowledged_by_role: 'caregiver',
        acknowledged_by: 'Test Caregiver B',
      }),
    })
    const line = screen.getByText(/^Acknowledged by/)
    expect(line).toHaveTextContent(/^Acknowledged by Test Caregiver B \(caregiver\), 3 min ago/)
    expect(line.querySelector('time')).toHaveAttribute('datetime', acknowledgedAt)
    expect(screen.getByRole('listitem')).not.toHaveTextContent(/care centre nurse|Message from the care centre/)
  })

  it('a caregiver acknowledge without a name falls back to "a caregiver"; a deleted account reads "Former caregiver"', () => {
    const fields = { alert_type: 'not_worn', status: 'acknowledged', acknowledged_at: NOW.toISOString(), acknowledged_by_role: 'caregiver' } as const
    renderRow({ alert: baseAlert(fields) })
    expect(screen.getByText(/^Acknowledged by/)).toHaveTextContent(/^Acknowledged by a caregiver \(caregiver\)/)
    cleanup()
    renderRow({ alert: baseAlert({ ...fields, acknowledged_by: 'Former caregiver' }) })
    expect(screen.getByText(/^Acknowledged by/)).toHaveTextContent(/^Acknowledged by Former caregiver \(caregiver\)/)
  })
})

describe('AlertRow ack_note', () => {
  const ackAt = () => new Date(NOW.getTime() - 60_000).toISOString()

  it('shows "Message from the care centre:" and the note when acknowledged with a note', () => {
    renderRow({
      alert: baseAlert({
        status: 'acknowledged',
        acknowledged_at: ackAt(),
        ack_note: 'Synthetic note: ambulance on the way, 8 minutes.',
      }),
    })
    const row = screen.getByRole('listitem')
    expect(within(row).getByText('Message from the care centre:')).toBeInTheDocument()
    const note = within(row).getByText('Synthetic note: ambulance on the way, 8 minutes.')
    expect(note.tagName).toBe('P')
    expect(note).toHaveClass('whitespace-pre-line')
    expect(note).toHaveClass('text-base')
  })

  it('keeps the line breaks of a multi-line note as plain text', () => {
    const text = 'Synthetic note: called the caregiver.\nWearable to be checked at 3 pm.'
    renderRow({ alert: baseAlert({ status: 'acknowledged', acknowledged_at: ackAt(), ack_note: text }) })
    const note = screen.getByText((_, el) => el?.tagName === 'P' && el.textContent === text)
    expect(note).toHaveClass('whitespace-pre-line')
    // One text node, no <br> or other markup.
    expect(note.children).toHaveLength(0)
  })

  it('renders markup in a note as text, not HTML', () => {
    const text = '<b>Synthetic</b> <script>note</script>'
    renderRow({ alert: baseAlert({ status: 'acknowledged', acknowledged_at: ackAt(), ack_note: text }) })
    const note = screen.getByText(text)
    expect(note.children).toHaveLength(0)
    expect(note.querySelector('b, script')).toBeNull()
  })

  it('shows the note on a cancelled alert that was acknowledged first', () => {
    renderRow({
      alert: baseAlert({
        status: 'cancelled',
        acknowledged_at: ackAt(),
        ack_note: 'Synthetic note: the care centre is calling the resident.',
        cancelled_at: NOW.toISOString(),
        cancelled_by_role: 'caregiver',
        cancelled_by: 'Test Caregiver A',
      }),
    })
    expect(screen.getByText('Message from the care centre:')).toBeInTheDocument()
    expect(screen.getByText('Synthetic note: the care centre is calling the resident.')).toBeInTheDocument()
  })

  it('shows no note without acknowledged_at, even when ack_note is set', () => {
    renderRow({ alert: baseAlert({ status: 'open', ack_note: 'Synthetic stray note' }) })
    const row = screen.getByRole('listitem')
    expect(row).not.toHaveTextContent('Message from the care centre')
    expect(row).not.toHaveTextContent('Synthetic stray note')
  })

  it('shows no note block when acknowledged without a note', () => {
    renderRow({ alert: baseAlert({ status: 'acknowledged', acknowledged_at: ackAt() }) })
    expect(screen.getByRole('listitem')).not.toHaveTextContent('Message from the care centre')
  })
})

describe('AlertRow cancel by role', () => {
  const cancelledAt = () => new Date(NOW.getTime() - 5 * 60_000).toISOString()

  it('a caregiver cancel shows the caregiver name and "(caregiver)"', () => {
    const at = cancelledAt()
    renderRow({
      alert: baseAlert({
        status: 'cancelled',
        cancelled_at: at,
        cancelled_by_role: 'caregiver',
        cancelled_by: 'Test Caregiver A',
      }),
    })
    const line = screen.getByText(/^Cancelled by/)
    expect(line).toHaveTextContent(/^Cancelled by Test Caregiver A \(caregiver\), 5 min ago \(/)
    expect(line.querySelector('time')).toHaveAttribute('datetime', at)
    expect(line).not.toHaveTextContent("I'm OK")
  })

  it('a caregiver cancel without a name falls back to "a caregiver"', () => {
    renderRow({ alert: baseAlert({ status: 'cancelled', cancelled_at: cancelledAt(), cancelled_by_role: 'caregiver' }) })
    expect(screen.getByText(/^Cancelled by/)).toHaveTextContent(/^Cancelled by a caregiver \(caregiver\), 5 min ago \(/)
  })

  it('a deleted caregiver account shows "Former caregiver"', () => {
    renderRow({
      alert: baseAlert({
        status: 'cancelled',
        cancelled_at: cancelledAt(),
        cancelled_by_role: 'caregiver',
        cancelled_by: 'Former caregiver',
      }),
    })
    expect(screen.getByText(/^Cancelled by/)).toHaveTextContent(/^Cancelled by Former caregiver \(caregiver\), 5 min ago \(/)
  })

  it('a resident cancel shows the "I\'m OK" line and ignores a stray cancelled_by', () => {
    renderRow({
      alert: baseAlert({
        status: 'cancelled',
        cancelled_at: cancelledAt(),
        cancelled_by_role: 'resident',
        cancelled_by: 'Test Caregiver A',
      }),
    })
    const line = screen.getByText(/^Cancelled by/)
    expect(line).toHaveTextContent(/^Cancelled by the resident \("I'm OK"\), 5 min ago \(/)
    expect(line).not.toHaveTextContent('Test Caregiver A')
  })

  it('a cancel after an acknowledge shows both the acknowledge and the cancel line', () => {
    renderRow({
      alert: baseAlert({
        status: 'cancelled',
        acknowledged_at: new Date(NOW.getTime() - 8 * 60_000).toISOString(),
        cancelled_at: cancelledAt(),
        cancelled_by_role: 'caregiver',
        cancelled_by: 'Test Caregiver B',
      }),
    })
    const row = screen.getByRole('listitem')
    expect(within(row).getByText(/^Acknowledged by/)).toHaveTextContent(
      /^Acknowledged by the care centre nurse, 8 min ago \(/,
    )
    expect(within(row).getByText(/^Cancelled by/)).toHaveTextContent(
      /^Cancelled by Test Caregiver B \(caregiver\), 5 min ago \(/,
    )
    expect(within(row).getByText('Cancelled')).toBeInTheDocument()
  })
})

describe('AlertRow escalated_at: at once vs after 5 minutes', () => {
  const escalatedAt = () => new Date(Date.now() - 30_000).toISOString()

  it('a critical alert reads "Sent to the care centre nurse at once"', () => {
    renderRow({ alert: baseAlert({ alert_type: 'fall', severity: 'critical', escalated_at: escalatedAt() }) })
    const line = screen.getByText('Sent to the care centre nurse at once')
    expect(line.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(screen.queryByText('Escalated to the care centre nurse')).not.toBeInTheDocument()
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
  })

  it('a warning from an unassigned device reads "Sent to the care centre nurse at once"', () => {
    render(
      <ul>
        <AlertRow
          alert={baseAlert({ resident_id: 'unassigned', severity: 'warning', escalated_at: escalatedAt() })}
          resident={undefined}
          now={NOW}
        />
      </ul>,
    )
    expect(screen.getByText('Sent to the care centre nurse at once')).toBeInTheDocument()
    expect(screen.queryByText('Escalated to the care centre nurse')).not.toBeInTheDocument()
  })

  it('a warning from an assigned device reads "Escalated to the care centre nurse"', () => {
    renderRow({ alert: baseAlert({ severity: 'warning', escalated_at: escalatedAt() }) })
    expect(screen.getByText('Escalated to the care centre nurse')).toBeInTheDocument()
    expect(screen.queryByText('Sent to the care centre nurse at once')).not.toBeInTheDocument()
  })

  it.each(['acknowledged', 'cancelled', 'resolved'] as const)(
    'a %s critical alert shows neither escalation line',
    (status) => {
      renderRow({ alert: baseAlert({ status, severity: 'critical', escalated_at: escalatedAt() }) })
      expect(screen.getByRole('listitem')).not.toHaveTextContent(/Sent to the care centre nurse|Escalat/)
    },
  )
})

describe('sentToNurseAtOnce', () => {
  it.each([
    ['critical', 'res-0008', true],
    ['critical', 'unassigned', true],
    ['warning', 'unassigned', true],
    ['warning', 'res-0008', false],
  ] as const)('severity %s, resident %s → %s', (severity, residentId, expected) => {
    expect(sentToNurseAtOnce(baseAlert({ severity, resident_id: residentId }))).toBe(expected)
  })
})

describe('AlertRow readerIsRecipient (the nurse inbox)', () => {
  const escalatedAt = () => new Date(Date.now() - 30_000).toISOString()
  const inTwoMinutes = () => new Date(Date.now() + 120_000).toISOString()

  it('defaults to false: the routing lines show for a reader who is not the recipient', () => {
    const { unmount } = renderRow({
      alert: baseAlert({ severity: 'critical', alert_type: 'fall', escalated_at: escalatedAt() }),
    })
    expect(screen.getByText('Sent to the care centre nurse at once')).toBeInTheDocument()
    unmount()
    const second = renderRow({ alert: baseAlert({ severity: 'warning', escalated_at: escalatedAt() }) })
    expect(screen.getByText('Escalated to the care centre nurse')).toBeInTheDocument()
    second.unmount()
    renderRow({ alert: baseAlert({ escalates_at: inTwoMinutes() }) })
    expect(screen.getByRole('timer')).toHaveTextContent(/^Escalates to the care centre nurse in \d:\d{2}$/)
    expect(screen.queryByText('Not yet escalated')).not.toBeInTheDocument()
  })

  it.each([
    ['a critical alert', { severity: 'critical', alert_type: 'fall' }],
    ['an unassigned warning', { severity: 'warning', resident_id: 'unassigned' }],
    ['an assigned warning', { severity: 'warning' }],
  ] as const)('hides the routing line for %s that has escalated, and adds no label', (_, fields) => {
    renderRow({ alert: baseAlert({ ...fields, escalated_at: escalatedAt() }), readerIsRecipient: true })
    const row = screen.getByRole('listitem')
    expect(row).not.toHaveTextContent(/Sent to the care centre nurse|at once|Escalat|Not yet escalated/)
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
  })

  it('shows a quiet "Not yet escalated" with no timer, instead of the countdown', () => {
    renderRow({ alert: baseAlert({ escalates_at: inTwoMinutes() }), readerIsRecipient: true })
    const label = screen.getByText('Not yet escalated')
    expect(label.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(label).toHaveClass('text-slate-600')
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    // (The row's own event time, e.g. "10:29 am", is still shown; only the countdown is gone.)
    expect(screen.getByRole('listitem')).not.toHaveTextContent(/Escalates|Escalated/)
  })

  it('"Not yet escalated" goes away when escalates_at passes, with nothing in its place', () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
    try {
      vi.setSystemTime(NOW)
      renderRow({
        alert: baseAlert({ escalates_at: new Date(NOW.getTime() + 3_000).toISOString() }),
        readerIsRecipient: true,
      })
      expect(screen.getByText('Not yet escalated')).toBeInTheDocument()
      act(() => vi.advanceTimersByTime(3_000))
      expect(screen.queryByText('Not yet escalated')).not.toBeInTheDocument()
      expect(screen.getByRole('listitem')).not.toHaveTextContent(/Escalat/)
    } finally {
      cleanup()
      vi.useRealTimers()
    }
  })

  it('no label on an alert that is not open, or has no escalates_at', () => {
    const { unmount } = renderRow({
      alert: baseAlert({ status: 'acknowledged', acknowledged_at: NOW.toISOString(), escalates_at: inTwoMinutes() }),
      readerIsRecipient: true,
    })
    expect(screen.queryByText('Not yet escalated')).not.toBeInTheDocument()
    unmount()
    renderRow({ alert: baseAlert(), readerIsRecipient: true })
    expect(screen.queryByText('Not yet escalated')).not.toBeInTheDocument()
  })

  it('still shows the acknowledge line and note', () => {
    renderRow({
      alert: baseAlert({
        status: 'acknowledged',
        severity: 'critical',
        escalated_at: escalatedAt(),
        acknowledged_at: new Date(NOW.getTime() - 60_000).toISOString(),
        ack_note: 'Synthetic note: on the way.',
      }),
      readerIsRecipient: true,
    })
    const row = screen.getByRole('listitem')
    expect(row).toHaveTextContent(/Acknowledged by the care centre nurse, 1 min ago/)
    expect(within(row).getByText('Synthetic note: on the way.')).toBeInTheDocument()
  })
})

describe('RoutingLine for a caregiver', () => {
  const renderLine = (alert: ResidentAlert) =>
    render(
      <div data-testid="line">
        <RoutingLine alert={alert} audience="caregiver" />
      </div>,
    )

  it('the countdown tells the caregiver how long they have to cancel, as m:ss', () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
    try {
      vi.setSystemTime(NOW)
      renderLine(baseAlert({ escalates_at: new Date(NOW.getTime() + 275_000).toISOString() }))
      expect(screen.getByRole('timer')).toHaveTextContent(
        /^The care centre nurse will be alerted in 4:35 unless you cancel this alert$/,
      )
      act(() => vi.advanceTimersByTime(210_000))
      expect(screen.getByRole('timer')).toHaveTextContent('in 1:05 unless')
      act(() => vi.advanceTimersByTime(65_000))
      expect(screen.queryByRole('timer')).not.toBeInTheDocument()
      expect(screen.getByTestId('line')).toHaveTextContent('Escalated to the care centre nurse')
    } finally {
      cleanup()
      vi.useRealTimers()
    }
  })

  it('shows the at-once and escalated lines, and nothing for an alert that is not open', () => {
    const { unmount } = renderLine(baseAlert({ severity: 'critical', escalated_at: NOW.toISOString() }))
    expect(screen.getByTestId('line')).toHaveTextContent('Sent to the care centre nurse at once')
    unmount()
    const second = renderLine(baseAlert({ escalated_at: NOW.toISOString() }))
    expect(screen.getByTestId('line')).toHaveTextContent('Escalated to the care centre nurse')
    second.unmount()
    renderLine(baseAlert({ status: 'cancelled', escalates_at: new Date(Date.now() + 60_000).toISOString() }))
    expect(screen.getByTestId('line')).toBeEmptyDOMElement()
  })
})

describe('AcknowledgedLines', () => {
  const renderLines = (alert: ResidentAlert) =>
    render(
      <div data-testid="lines">
        <AcknowledgedLines alert={alert} now={NOW} />
      </div>,
    )

  it('renders nothing when the alert was never acknowledged', () => {
    renderLines(baseAlert({ ack_note: 'Synthetic stray note' }))
    expect(screen.getByTestId('lines')).toBeEmptyDOMElement()
  })

  it('renders the acknowledge line, and the note block only with a note', () => {
    const acknowledgedAt = new Date(NOW.getTime() - 3 * 60_000).toISOString()
    const { unmount } = renderLines(baseAlert({ status: 'acknowledged', acknowledged_at: acknowledgedAt }))
    expect(screen.getByTestId('lines')).toHaveTextContent(/^Acknowledged by the care centre nurse, 3 min ago/)
    expect(screen.getByTestId('lines')).not.toHaveTextContent('Message from the care centre')
    unmount()

    renderLines(baseAlert({ status: 'acknowledged', acknowledged_at: acknowledgedAt, ack_note: 'Synthetic note' }))
    expect(screen.getByText('Message from the care centre:')).toBeInTheDocument()
    expect(screen.getByText('Synthetic note')).toHaveClass('whitespace-pre-line')
  })
})

describe('acknowledge note form (alert-actions-ui, criteria 1-7)', () => {
  const CONTEXT = 'Low blood oxygen, Test Resident H'
  const ackButton = () => screen.getByRole('button', { name: `Acknowledge: ${CONTEXT}` })
  const field = () => screen.getByRole('textbox', { name: "Note for the resident's caregivers (optional)" })

  function setup() {
    const user = userEvent.setup()
    const onAcknowledge = vi.fn()
    const onResolve = vi.fn()
    renderRow({ actions: { onAcknowledge, onResolve } })
    return { user, onAcknowledge, onResolve }
  }

  it('Acknowledge opens the form with the field focused, the hint, and both buttons; Resolve stays', async () => {
    const { user, onAcknowledge } = setup()
    await user.click(ackButton())
    expect(onAcknowledge).not.toHaveBeenCalled()
    const form = screen.getByRole('form', { name: `Acknowledge: ${CONTEXT}` })
    expect(field()).toHaveFocus()
    expect(field()).toHaveAttribute('maxLength', '500')
    expect(field()).toHaveAccessibleDescription(
      "Emailed to this resident's caregivers and shown to them in the portal.",
    )
    expect(within(form).getByRole('button', { name: `Acknowledge: ${CONTEXT}` })).toHaveAttribute('type', 'submit')
    expect(within(form).getByRole('button', { name: /^Cancel/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `Resolve: ${CONTEXT}` })).toBeInTheDocument()
    // The opening button is replaced by the form's own submit button.
    expect(screen.getAllByRole('button', { name: `Acknowledge: ${CONTEXT}` })).toHaveLength(1)
  })

  it('Cancel closes the form without acknowledging and returns focus to Acknowledge', async () => {
    const { user, onAcknowledge } = setup()
    await user.click(ackButton())
    await user.type(field(), 'Synthetic draft')
    await user.click(screen.getByRole('button', { name: /^Cancel/ }))
    expect(onAcknowledge).not.toHaveBeenCalled()
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(ackButton()).toHaveFocus()
    expect(screen.getByRole('listitem')).not.toHaveTextContent('Synthetic draft')
  })

  it('submitting an empty or whitespace-only note acknowledges with no note', async () => {
    const { user, onAcknowledge } = setup()
    await user.click(ackButton())
    await user.type(field(), '   ')
    await user.click(ackButton())
    expect(onAcknowledge).toHaveBeenCalledExactlyOnceWith(undefined)
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: `Resolve: ${CONTEXT}` })).toHaveFocus()
  })

  it('submitting a note passes it normalised (CRLF to LF, trimmed)', async () => {
    const { user, onAcknowledge } = setup()
    await user.click(ackButton())
    fireEvent.change(field(), { target: { value: '  Synthetic note: on the way.\r\nAbout 8 minutes.  ' } })
    await user.click(ackButton())
    expect(onAcknowledge).toHaveBeenCalledExactlyOnceWith('Synthetic note: on the way.\nAbout 8 minutes.')
  })

  it('shows "N of 500 characters" only from 400 characters, linked to the field', async () => {
    const { user } = setup()
    await user.click(ackButton())
    fireEvent.change(field(), { target: { value: 'a'.repeat(399) } })
    expect(screen.queryByText(/of 500 characters/)).not.toBeInTheDocument()
    fireEvent.change(field(), { target: { value: 'a'.repeat(400) } })
    const count = screen.getByText('400 of 500 characters')
    expect(field().getAttribute('aria-describedby')?.split(' ')).toContain(count.id)
    fireEvent.change(field(), { target: { value: 'a'.repeat(500) } })
    expect(screen.getByText('500 of 500 characters')).toBeInTheDocument()
  })

  it.each([
    ['a tab', 'Synthetic\tnote'],
    ['a zero-width space', 'Synthetic\u200bnote'],
    ['a bidi override', 'Synthetic \u202enote'],
  ])('refuses %s with an inline error, focus in the field, and no acknowledge', async (_, value) => {
    const { user, onAcknowledge } = setup()
    await user.click(ackButton())
    fireEvent.change(field(), { target: { value } })
    await user.click(ackButton())
    expect(onAcknowledge).not.toHaveBeenCalled()
    const error = screen.getByText('Remove hidden or control characters from the note.')
    expect(field()).toHaveAttribute('aria-invalid', 'true')
    expect(field().getAttribute('aria-describedby')?.split(' ')).toContain(error.id)
    expect(field()).toHaveFocus()
    // Editing clears the error.
    await user.type(field(), 'x')
    expect(screen.queryByText('Remove hidden or control characters from the note.')).not.toBeInTheDocument()
    expect(field()).not.toHaveAttribute('aria-invalid')
  })

  it('an acknowledged alert has no Acknowledge button, so no form', () => {
    renderRow({
      alert: baseAlert({ status: 'acknowledged', acknowledged_at: NOW.toISOString() }),
      actions: { onAcknowledge: vi.fn(), onResolve: vi.fn() },
    })
    expect(screen.queryByRole('button', { name: /^Acknowledge/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
  })

  it('never shows a Cancel alert button (cancel is for caregivers only)', () => {
    renderRow({ actions: { onAcknowledge: vi.fn(), onResolve: vi.fn() } })
    expect(screen.queryByRole('button', { name: /Cancel alert/ })).not.toBeInTheDocument()
  })

  it('has no axe violations with the form open and showing an error', async () => {
    const { user } = setup()
    await user.click(ackButton())
    fireEvent.change(field(), { target: { value: 'Synthetic\tnote' } })
    await user.click(ackButton())
    expect(screen.getByText('Remove hidden or control characters from the note.')).toBeInTheDocument()
    // The rendered container, as the other axe tests do (a lone row has no page landmarks).
    expect(await axeViolations(screen.getByRole('listitem').closest('ul') as HTMLElement)).toEqual([])
  }, 30_000)
})

describe('CancelledLine and ResolvedLine (used by AlertRow and the caregiver view)', () => {
  it('a caregiver cancel with an empty name falls back to "a caregiver"', () => {
    renderRow({
      alert: baseAlert({ status: 'cancelled', cancelled_at: NOW.toISOString(), cancelled_by_role: 'caregiver', cancelled_by: '' }),
    })
    expect(screen.getByRole('listitem')).toHaveTextContent('Cancelled by a caregiver (caregiver), just now')
  })
})
