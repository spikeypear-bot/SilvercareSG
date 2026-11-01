import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { demoDataFor } from '../../test/dashboardMock'
import type { ResidentAlert } from '../../types/dashboard'
import { AlertRow } from './AlertRow'

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
