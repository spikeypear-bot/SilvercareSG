import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { demoDataFor } from '../../test/dashboardMock'
import type { ResidentAlert } from '../../types/dashboard'
import { AlertRow } from './AlertRow'
import { ALERT_TYPE_LABELS } from './labels'

const NOW = new Date('2026-10-18T02:30:00Z')

function baseAlert(overrides: Partial<ResidentAlert> = {}): ResidentAlert {
  return {
    alert_id: 'alert-test-001',
    resident_id: 'res-0008',
    device_id: 'bedroom-test-008',
    alert_type: 'possible_fall',
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
    await user.click(screen.getByRole('button', { name: 'Acknowledge: Possible fall, Test Resident H' }))
    expect(onAcknowledge).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Resolve: Possible fall, Test Resident H' }))
    expect(onResolve).toHaveBeenCalledTimes(1)
  })

  it('shows nothing about acknowledgement when by/at are missing', () => {
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
    ['inactivity', 'Inactivity'],
    ['device_offline', 'Device offline'],
    ['low_battery', 'Low battery'],
  ] as const)('%s shows "%s" and names the buttons with it', (type, label) => {
    renderRow({ alert: baseAlert({ alert_type: type }), actions: { onAcknowledge: vi.fn(), onResolve: vi.fn() } })
    const row = screen.getByRole('listitem')
    expect(within(row).getByText(label)).toBeInTheDocument()
    expect(row).not.toHaveTextContent('Vitals need attention')
    expect(screen.getByRole('button', { name: `Resolve: ${label}, Test Resident H` })).toBeInTheDocument()
  })

  it('every alert type has a label', () => {
    Object.values(ALERT_TYPE_LABELS).forEach((label) => expect(label.trim()).not.toBe(''))
    expect(Object.keys(ALERT_TYPE_LABELS)).toHaveLength(10)
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

  it('shows "Escalated to nurses" from escalated_at alone (no escalates_at)', () => {
    renderRow({ alert: baseAlert({ escalated_at: past() }) })
    const row = screen.getByRole('listitem')
    expect(within(row).getByText('Escalated to nurses')).toBeInTheDocument()
    expect(within(row).getByText('Escalated to nurses').querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
  })

  it('escalated_at wins over a future escalates_at: no countdown', () => {
    renderRow({ alert: baseAlert({ escalated_at: past(), escalates_at: future() }) })
    expect(screen.getAllByText('Escalated to nurses')).toHaveLength(1)
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

  it('rounds partial seconds up and flips to "Escalated to nurses" at zero', () => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(NOW.getTime() + 2_500).toISOString() }) })
    expect(screen.getByRole('timer')).toHaveTextContent('Escalates to nurses in 3 s')
    tick(1000)
    expect(screen.getByRole('timer')).toHaveTextContent('Escalates to nurses in 2 s')
    tick(1000)
    expect(screen.getByRole('timer')).toHaveTextContent('Escalates to nurses in 1 s')
    tick(1000)
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.getByText('Escalated to nurses')).toBeInTheDocument()
    tick(10_000)
    expect(screen.getByText('Escalated to nurses')).toBeInTheDocument()
  })

  it('shows "Escalated to nurses" straight away for an alert already past its deadline', () => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(NOW.getTime() - 1_000).toISOString() }) })
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.getByText('Escalated to nurses')).toBeInTheDocument()
  })

  it('an escalates_at exactly now counts as escalated', () => {
    renderRow({ alert: baseAlert({ escalates_at: NOW.toISOString() }) })
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.getByText('Escalated to nurses')).toBeInTheDocument()
  })

  it('starts at the API escalates_at, e.g. 30 s left for an alert raised 30 s ago', () => {
    renderRow({ alert: baseAlert({ escalates_at: new Date(Date.parse(baseAlert().event_ts) + 60_000).toISOString() }) })
    expect(screen.getByRole('timer')).toHaveTextContent('Escalates to nurses in 30 s')
    tick(29_000)
    expect(screen.getByRole('timer')).toHaveTextContent('Escalates to nurses in 1 s')
    tick(1000)
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.getByText('Escalated to nurses')).toBeInTheDocument()
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
    const escalated = screen.getByText('Escalated to nurses')
    expect(escalated).toHaveClass('text-base')
    expect(escalated.querySelector('svg')).toHaveClass('size-5')

    rerender(
      <ul>
        <AlertRow
          alert={baseAlert({ status: 'acknowledged', acknowledged_by: 'Test Nurse B', acknowledged_at: NOW.toISOString() })}
          resident={undefined}
          now={NOW}
        />
      </ul>,
    )
    expect(screen.getByText(/^Acknowledged by Test Nurse B,/)).toHaveClass('text-base')
  })
})
