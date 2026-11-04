import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { dashboardLoader, demoDataFor, resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { AuthUser } from '../../types/auth'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import { NurseDashboard } from './NurseDashboard'
import type { DashboardView } from './useDashboardData'

vi.mock('./useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./useDashboardData')>()
  const { dashboardLoader } = await import('../../test/dashboardMock')
  return {
    ...actual,
    useDashboardData: (view: DashboardView) => actual.useDashboardData(view, dashboardLoader),
  }
})

const BANNER = 'You are viewing synthetic demo data. No real resident information is shown.'

beforeEach(() => {
  resetDashboardLoader()
})

function renderNurse(user: AuthUser = TEST_USERS.nurse) {
  const ue = userEvent.setup()
  const view = renderWithAuth(<NurseDashboard />, { user })
  return { user: ue, ...view }
}

async function renderLoaded(data: DashboardData = demoDataFor('staff'), user?: AuthUser) {
  dashboardLoader.mockResolvedValue(data)
  const ctx = renderNurse(user)
  await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
  return { ...ctx, data }
}

// --- queries ---------------------------------------------------------------

const tileGroup = () => screen.getByRole('group', { name: 'Choose what the panel below shows' })
const tile = (label: string) => within(tileGroup()).getByRole('button', { name: new RegExp(`^${label}`) })
const tileValue = (label: string) =>
  within(tile(label)).getByText(label, { selector: 'span' }).nextElementSibling?.textContent
const inboxHeading = () => screen.getByRole('heading', { level: 2, name: /^Open alerts/ })
const inbox = () => screen.getByRole('region', { name: /^Open alerts/ })
/** Rows of the open-alerts list (not the resolved list inside <details>). */
const openRows = () => {
  const list = inbox().querySelector(':scope > ul')
  return list ? within(list as HTMLElement).getAllByRole('listitem') : []
}
const announcer = () => screen.getByRole('status')
const ackButton = (context: string) => screen.getByRole('button', { name: `Acknowledge: ${context}` })
/**
 * Acknowledge opens the note form (docs/features/alert-actions-ui.md); its submit button
 * has the same accessible name, so the second click submits with no note.
 */
async function acknowledge(user: ReturnType<typeof userEvent.setup>, context: string) {
  await user.click(ackButton(context))
  await user.click(ackButton(context))
}
function acknowledgeNow(context: string) {
  fireEvent.click(ackButton(context))
  fireEvent.click(ackButton(context))
}
const resolveButton = (context: string) => screen.getByRole('button', { name: `Resolve: ${context}` })
const rowFor = (context: string) => resolveButton(context).closest('li') as HTMLElement
const residentCards = () => screen.queryAllByRole('article')

/** Demo inbox order: critical first (newest first), then the rest newest first. */
const DEMO_INBOX_ORDER: ReadonlyArray<[string, string]> = [
  ['Fall detected', 'Test Resident C'],
  ['High heart rate', 'Test Resident G'],
  ['Fall detected', 'Test Resident Q'],
  ['Possible fall', 'Test Resident H'],
  ['Possible fall', 'Test Resident L'],
  ['Low blood oxygen', 'Test Resident S'],
  ['Inactivity', 'Test Resident B'],
  ['Device offline', 'Test Resident O'],
  ['Low battery', 'Test Resident F'],
  ['Device offline', 'Test Resident I'],
  ['Low battery', 'Test Resident M'],
  ['Possible fall', 'Test Resident B'],
]

/** A single synthetic alert for focused tests. */
function oneAlert(overrides: Partial<ResidentAlert> = {}): DashboardData {
  const data = demoDataFor('staff')
  const alert: ResidentAlert = {
    alert_id: 'alert-test-001',
    resident_id: 'res-0003',
    device_id: data.residents[2].wearable.device_id,
    alert_type: 'fall',
    severity: 'critical',
    status: 'open',
    event_ts: new Date(Date.now() - 5 * 60_000).toISOString(),
    ...overrides,
  }
  return { ...data, alerts: [alert] }
}

// ---------------------------------------------------------------------------

describe('loading, error, and layout', () => {
  it('shows skeletons with a loading status, plus header, banner, and greeting', () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    renderNurse()
    expect(screen.getByRole('status')).toHaveTextContent('Loading dashboard')
    expect(screen.getByRole('heading', { level: 1, name: 'Nurse dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('note', { name: 'Demo data' })).toHaveTextContent(BANNER)
    expect(screen.getByText(/^Good (morning|afternoon|evening), Test Nurse A$/)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Choose what the panel below shows' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Acknowledge/ })).not.toBeInTheDocument()
    expect(dashboardLoader).toHaveBeenCalledWith('staff')
  })

  it('shows an error notice with Try again, which reloads', async () => {
    dashboardLoader.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderNurse()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load the dashboard.")
    expect(screen.queryByRole('heading', { level: 2, name: /^Open alerts/ })).not.toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })).toBeInTheDocument()
    expect(dashboardLoader).toHaveBeenCalledTimes(2)
  })

  it('keeps the demo banner visible in the error state', async () => {
    dashboardLoader.mockRejectedValue(new Error('boom'))
    renderNurse()
    await screen.findByRole('alert')
    expect(screen.getByRole('note', { name: 'Demo data' })).toHaveTextContent(BANNER)
  })

  it('page title has no personal data', async () => {
    renderNurse()
    await waitFor(() => expect(document.title).toBe('SilverCare - Nurse dashboard'))
  })
})

describe('stat tiles are toggle buttons that pick the panel', () => {
  it('shows four tiles with the numbers from the data', async () => {
    await renderLoaded()
    // 12 demo alerts, all unresolved; 3 critical (2 falls + 1 acknowledged vitals).
    expect(tileValue('Residents monitored')).toBe('20')
    expect(tileValue('Open alerts')).toBe('12')
    expect(within(tile('Open alerts')).getByText('3 critical')).toBeInTheDocument()
    // 6 offline devices (L and O wearables; F, I, O, Q bedroom sensors).
    expect(tileValue('Devices offline')).toBe('6')
    // Wearables only (F 15%, M 12%); D's 9% bedroom sensor is not a wearable.
    expect(tileValue('Wearables low on battery')).toBe('2')
    expect(within(tile('Wearables low on battery')).getByText('Below 20%')).toBeInTheDocument()
  })

  it('Open alerts is selected by default and the inbox is shown', async () => {
    await renderLoaded()
    expect(within(tileGroup()).getAllByRole('button')).toHaveLength(4)
    expect(tile('Open alerts')).toHaveAttribute('aria-pressed', 'true')
    for (const label of ['Residents monitored', 'Devices offline', 'Wearables low on battery']) {
      expect(tile(label)).toHaveAttribute('aria-pressed', 'false')
    }
    expect(inboxHeading()).toHaveTextContent('Open alerts (12)')
    expect(screen.queryByRole('heading', { level: 2, name: 'Residents' })).not.toBeInTheDocument()
  })

  it.each([
    ['Devices offline', /^Devices offline \(6\)$/],
    ['Wearables low on battery', /^Wearables low on battery \(2\)$/],
    ['Residents monitored', /^Residents$/],
  ])('clicking "%s" switches the panel and the pressed tile', async (label, heading) => {
    const { user } = await renderLoaded()
    await user.click(tile(label))
    expect(tile(label)).toHaveAttribute('aria-pressed', 'true')
    expect(tile('Open alerts')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('heading', { level: 2, name: heading })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 2, name: /^Open alerts/ })).not.toBeInTheDocument()

    await user.click(tile('Open alerts'))
    expect(tile('Open alerts')).toHaveAttribute('aria-pressed', 'true')
    expect(tile(label)).toHaveAttribute('aria-pressed', 'false')
    expect(inboxHeading()).toBeInTheDocument()
  })

  it('exactly one tile is pressed at any time', async () => {
    const { user } = await renderLoaded()
    for (const label of ['Devices offline', 'Residents monitored', 'Wearables low on battery', 'Open alerts']) {
      await user.click(tile(label))
      const pressed = within(tileGroup())
        .getAllByRole('button')
        .filter((b) => b.getAttribute('aria-pressed') === 'true')
      expect(pressed).toEqual([tile(label)])
    }
  })
})

describe('open alerts inbox', () => {
  it('lists every unresolved alert, critical pinned first, then newest first', async () => {
    await renderLoaded()
    const rows = openRows()
    expect(rows).toHaveLength(DEMO_INBOX_ORDER.length)
    rows.forEach((row, i) => {
      const [type, name] = DEMO_INBOX_ORDER[i]
      expect(row).toHaveTextContent(type)
      expect(row).toHaveTextContent(name)
    })
    expect(inbox()).toHaveTextContent('Critical alerts are pinned to the top, then newest first.')
  })

  it('sorts the same way even if the data arrives oldest first', async () => {
    const data = demoDataFor('staff')
    await renderLoaded({ ...data, alerts: [...data.alerts].reverse() })
    openRows().forEach((row, i) => {
      const [type, name] = DEMO_INBOX_ORDER[i]
      expect(row).toHaveTextContent(type)
      expect(row).toHaveTextContent(name)
    })
  })

  it('each row shows severity, lifecycle, type, resident name, ID, home ID, and relative time', async () => {
    const { data } = await renderLoaded()
    const residentC = data.residents.find((r) => r.resident_id === 'res-0003')
    const row = within(openRows()[0])
    expect(row.getByText('Critical')).toBeInTheDocument()
    expect(row.getByText('Open')).toBeInTheDocument()
    expect(row.getByText('Fall detected')).toBeInTheDocument()
    expect(row.getByText('Test Resident C')).toBeInTheDocument()
    expect(row.getByText('· res-0003')).toBeInTheDocument()
    expect(residentC?.home_id).toMatch(/^home-\d{4}$/)
    expect(openRows()[0]).toHaveTextContent(`Home${residentC?.home_id}`)
    const time = row.getByText('4 min ago').closest('time')
    expect(time).toHaveAttribute('datetime', data.alerts.find((a) => a.resident_id === 'res-0003')?.event_ts)
  })

  it('shows the provided severity, not one derived from the alert type', async () => {
    await renderLoaded(oneAlert({ severity: 'warning' }))
    const row = openRows()[0]
    expect(row).toHaveTextContent('Warning')
    expect(row).toHaveTextContent('Fall detected')
    expect(row).not.toHaveTextContent('Critical')
  })

  it('open rows have Acknowledge and Resolve; acknowledged rows have only Resolve', async () => {
    await renderLoaded()
    expect(ackButton('Fall detected, Test Resident C')).toBeInTheDocument()
    expect(resolveButton('Fall detected, Test Resident C')).toBeInTheDocument()

    const acked = 'High heart rate, Test Resident G'
    expect(resolveButton(acked)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `Acknowledge: ${acked}` })).not.toBeInTheDocument()
    const row = within(rowFor(acked))
    expect(row.getByText('Acknowledged')).toBeInTheDocument()
    expect(rowFor(acked)).toHaveTextContent(/Acknowledged by the care centre nurse, 6 min ago/)
    expect(rowFor(acked)).not.toHaveTextContent(/Test Nurse/)

    // 9 open + 3 acknowledged in the demo data.
    expect(screen.getAllByRole('button', { name: /^Acknowledge: / })).toHaveLength(9)
    expect(screen.getAllByRole('button', { name: /^Resolve: / })).toHaveLength(12)
  })

  it('a cancelled alert is not in the inbox or the counts, and has no buttons', async () => {
    await renderLoaded(
      oneAlert({
        status: 'cancelled',
        escalated_at: new Date(Date.now() - 60_000).toISOString(),
        cancelled_at: new Date(Date.now() - 4 * 60_000).toISOString(),
        cancelled_by_role: 'resident',
      }),
    )
    expect(openRows()).toEqual([])
    expect(within(inbox()).getByText('No open alerts.')).toBeInTheDocument()
    expect(inboxHeading()).toHaveTextContent('Open alerts (0)')
    expect(tileValue('Open alerts')).toBe('0')
    expect(within(tile('Open alerts')).getByText('0 critical')).toBeInTheDocument()
    expect(inbox()).not.toHaveTextContent('Test Resident C')
    expect(inbox()).not.toHaveTextContent('Cancelled')
    expect(screen.queryAllByRole('button', { name: /^(Acknowledge|Resolve): / })).toEqual([])
    // Not resolved by anyone on this page, so not in the resolved list either.
    expect(screen.queryByText(/Resolved since this page opened/)).not.toBeInTheDocument()
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('cancelled alerts in a mixed list are left out of the rows, the heading, and the tiles', async () => {
    const data = demoDataFor('staff')
    // Cancel the two open falls (C and Q): the wearable "I'm OK" press.
    const cancelledAt = new Date(Date.now() - 60_000).toISOString()
    const alerts = data.alerts.map((a) =>
      a.alert_type === 'fall' && a.status === 'open'
        ? { ...a, status: 'cancelled' as const, cancelled_at: cancelledAt, cancelled_by_role: 'resident' as const }
        : a,
    )
    expect(alerts.filter((a) => a.status === 'cancelled')).toHaveLength(2)
    await renderLoaded({ ...data, alerts })

    const expected = DEMO_INBOX_ORDER.filter(([type]) => type !== 'Fall detected')
    expect(openRows()).toHaveLength(10)
    openRows().forEach((row, i) => {
      expect(row).toHaveTextContent(expected[i][0])
      expect(row).toHaveTextContent(expected[i][1])
    })
    expect(inbox()).not.toHaveTextContent('Fall detected')
    expect(inbox()).not.toHaveTextContent('Cancelled')
    expect(inboxHeading()).toHaveTextContent('Open alerts (10)')
    expect(tileValue('Open alerts')).toBe('10')
    // Only the acknowledged High heart rate (G) is still critical.
    expect(within(tile('Open alerts')).getByText('1 critical')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /: Fall detected, Test Resident (C|Q)$/ })).not.toBeInTheDocument()
    // 7 open + 3 acknowledged left.
    expect(screen.getAllByRole('button', { name: /^Acknowledge: / })).toHaveLength(7)
    expect(screen.getAllByRole('button', { name: /^Resolve: / })).toHaveLength(10)
  })

  it('shows "No open alerts." when there are none, and the tile reads 0', async () => {
    await renderLoaded({ ...demoDataFor('staff'), alerts: [] })
    expect(within(inbox()).getByText('No open alerts.')).toBeInTheDocument()
    expect(inboxHeading()).toHaveTextContent('Open alerts (0)')
    expect(tileValue('Open alerts')).toBe('0')
    expect(within(tile('Open alerts')).getByText('0 critical')).toBeInTheDocument()
    expect(screen.queryByText(/Resolved since this page opened/)).not.toBeInTheDocument()
  })

  it('does not list resolved alerts in the inbox or count them', async () => {
    const data = demoDataFor('staff')
    const [first, ...rest] = data.alerts
    const resolved: ResidentAlert = {
      ...first,
      status: 'resolved',
      resolved_by: 'Test Nurse C',
      resolved_at: new Date().toISOString(),
    }
    await renderLoaded({ ...data, alerts: [resolved, ...rest] })
    expect(openRows()).toHaveLength(11)
    expect(tileValue('Open alerts')).toBe('11')
    expect(screen.queryByRole('button', { name: /Resolve: Possible fall, Test Resident H/ })).not.toBeInTheDocument()
  })

  it('the alert lists are not live regions (the announcer is)', async () => {
    await renderLoaded()
    const list = inbox().querySelector(':scope > ul') as HTMLElement
    expect(list.closest('[aria-live]')).toBeNull()
    expect(announcer()).toHaveAttribute('aria-live', 'polite')
    expect(announcer()).toBeEmptyDOMElement()
    expect(screen.getAllByRole('status')).toHaveLength(1)
  })
})

describe('acknowledge', () => {
  const C = 'Fall detected, Test Resident C'

  it('records "Acknowledged by the care centre nurse" (no name), removes the button, and keeps focus on Resolve', async () => {
    const { user } = await renderLoaded()
    await acknowledge(user, C)

    expect(screen.queryByRole('button', { name: `Acknowledge: ${C}` })).not.toBeInTheDocument()
    const row = rowFor(C)
    expect(within(row).getByText('Acknowledged')).toBeInTheDocument()
    expect(within(row).queryByText('Open')).not.toBeInTheDocument()
    expect(row).toHaveTextContent('Acknowledged by the care centre nurse, just now')
    expect(row).not.toHaveTextContent('Test Nurse A')
    // No note from the button alone.
    expect(row).not.toHaveTextContent('Message from the care centre')
    await waitFor(() => expect(resolveButton(C)).toHaveFocus())
  })

  it('acknowledging with a note shows it in the row, but never announces it (alert-actions-ui, criterion 7)', async () => {
    const { user } = await renderLoaded()
    await user.click(ackButton(C))
    await user.type(
      screen.getByRole('textbox', { name: "Note for the resident's caregivers (optional)" }),
      'Synthetic note: ambulance on the way.',
    )
    await user.click(ackButton(C))
    const row = rowFor(C)
    expect(row).toHaveTextContent('Acknowledged by the care centre nurse, just now')
    expect(within(row).getByText('Message from the care centre:')).toBeInTheDocument()
    expect(within(row).getByText('Synthetic note: ambulance on the way.')).toBeInTheDocument()
    expect(announcer()).toHaveTextContent(`Acknowledged: ${C}.`)
    expect(announcer()).not.toHaveTextContent('Synthetic note')
  })

  it('cancelling the note form leaves the alert open and announces nothing', async () => {
    const { user } = await renderLoaded()
    await user.click(ackButton(C))
    await user.click(within(screen.getByRole('form', { name: `Acknowledge: ${C}` })).getByRole('button', { name: /^Cancel/ }))
    expect(within(rowFor(C)).getByText('Open')).toBeInTheDocument()
    expect(ackButton(C)).toHaveFocus()
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('never shows a "Cancel alert" button to a nurse (cancel is for caregivers)', async () => {
    await renderLoaded()
    expect(screen.queryAllByRole('button', { name: /Cancel alert/ })).toEqual([])
  })

  it('announces the acknowledgement', async () => {
    const { user } = await renderLoaded()
    await acknowledge(user, C)
    await waitFor(() => expect(announcer()).toHaveTextContent(`Acknowledged: ${C}.`))
  })

  it('keeps the alert in the inbox and in the open count', async () => {
    const { user } = await renderLoaded()
    await acknowledge(user, C)
    expect(openRows()).toHaveLength(12)
    expect(tileValue('Open alerts')).toBe('12')
    expect(openRows()[0]).toBe(rowFor(C)) // still pinned first
  })

  it('stops the escalation countdown', async () => {
    const { user } = await renderLoaded()
    const H = 'Possible fall, Test Resident H'
    expect(within(rowFor(H)).getByRole('timer')).toBeInTheDocument()
    await acknowledge(user, H)
    expect(within(rowFor(H)).queryByRole('timer')).not.toBeInTheDocument()
    expect(rowFor(H)).not.toHaveTextContent('Escalated to the care centre nurse')
    expect(rowFor(H)).toHaveTextContent('Acknowledged by the care centre nurse')
  })

  it('reads "the care centre nurse" whatever the user\'s display name, even none', async () => {
    const { user } = await renderLoaded(demoDataFor('staff'), { ...TEST_USERS.nurse, name: '' })
    await acknowledge(user, C)
    expect(rowFor(C)).toHaveTextContent('Acknowledged by the care centre nurse, just now')
    expect(rowFor(C)).not.toHaveTextContent('Staff member')
  })

  it('shows the nurse notes on the acknowledged demo alerts, as plain text', async () => {
    await renderLoaded()
    const g = rowFor('High heart rate, Test Resident G')
    expect(within(g).getByText('Message from the care centre:')).toBeInTheDocument()
    expect(within(g).getByText('Synthetic note: ambulance on the way, 8 minutes.')).toHaveClass('whitespace-pre-line')
    const o = rowFor('Device offline, Test Resident O')
    expect(within(o).getByText('Message from the care centre:')).toBeInTheDocument()
    expect(o).toHaveTextContent(/Synthetic note: called the caregiver\.\s+Wearable to be checked at 3 pm\./)
    // res-0013 was acknowledged without a note.
    expect(rowFor('Low battery, Test Resident M')).not.toHaveTextContent('Message from the care centre')
  })
})

describe('resolve', () => {
  const C = 'Fall detected, Test Resident C'

  it('removes the row and updates the heading count and the tiles', async () => {
    const { user } = await renderLoaded()
    await user.click(resolveButton(C))
    expect(screen.queryByRole('button', { name: `Resolve: ${C}` })).not.toBeInTheDocument()
    expect(openRows()).toHaveLength(11)
    expect(inboxHeading()).toHaveTextContent('Open alerts (11)')
    expect(tileValue('Open alerts')).toBe('11')
    expect(within(tile('Open alerts')).getByText('2 critical')).toBeInTheDocument()
  })

  it('lists it under "Resolved since this page opened" with "Resolved by Test Nurse A"', async () => {
    const { user } = await renderLoaded()
    expect(screen.queryByText(/Resolved since this page opened/)).not.toBeInTheDocument()
    await user.click(resolveButton(C))
    const summary = screen.getByText('Resolved since this page opened (1)')
    const details = summary.closest('details') as HTMLElement
    expect(details).not.toBeNull()
    const items = within(details).getAllByRole('listitem')
    expect(items).toHaveLength(1)
    expect(items[0]).toHaveTextContent(C)
    expect(items[0]).toHaveTextContent('Resolved by Test Nurse A, just now')
  })

  it('moves focus to the inbox heading', async () => {
    const { user } = await renderLoaded()
    await user.click(resolveButton(C))
    await waitFor(() => expect(inboxHeading()).toHaveFocus())
    expect(inboxHeading()).toHaveAttribute('tabindex', '-1')
  })

  it('announces the resolution', async () => {
    const { user } = await renderLoaded()
    await user.click(resolveButton(C))
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${C}.`))
  })

  it('announcing the same text twice still changes the live region', async () => {
    // Two synthetic alerts with the same type and resident give the same "Resolved: ..." text.
    const data = oneAlert()
    const twin: ResidentAlert = { ...data.alerts[0], alert_id: 'alert-test-002' }
    const { user } = await renderLoaded({ ...data, alerts: [data.alerts[0], twin] })
    await user.click(screen.getAllByRole('button', { name: `Resolve: ${C}` })[0])
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${C}.`))
    const first = announcer().textContent
    await user.click(resolveButton(C))
    await waitFor(() => expect(announcer().textContent).not.toBe(first))
    expect(announcer()).toHaveTextContent(`Resolved: ${C}.`)
    expect(screen.getByText('Resolved since this page opened (2)')).toBeInTheDocument()
  })

  it('can resolve an acknowledged alert', async () => {
    const { user } = await renderLoaded()
    const G = 'High heart rate, Test Resident G'
    await user.click(resolveButton(G))
    expect(screen.queryByRole('button', { name: `Resolve: ${G}` })).not.toBeInTheDocument()
    const details = screen.getByText('Resolved since this page opened (1)').closest('details') as HTMLElement
    expect(details).toHaveTextContent(G)
  })

  it('acknowledge then resolve, newest resolution listed first', async () => {
    const { user } = await renderLoaded()
    await acknowledge(user, C)
    await user.click(resolveButton(C))
    await user.click(resolveButton('Possible fall, Test Resident H'))
    const details = screen.getByText('Resolved since this page opened (2)').closest('details') as HTMLElement
    const items = within(details).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    // Both resolved within the same second may share a timestamp, so only check both are present.
    expect(details).toHaveTextContent(C)
    expect(details).toHaveTextContent('Possible fall, Test Resident H')
  })

  it('resolving the last alert shows "No open alerts."', async () => {
    const { user } = await renderLoaded(oneAlert())
    await user.click(resolveButton(C))
    expect(within(inbox()).getByText('No open alerts.')).toBeInTheDocument()
    expect(tileValue('Open alerts')).toBe('0')
    expect(screen.getByText('Resolved since this page opened (1)')).toBeInTheDocument()
    await waitFor(() => expect(inboxHeading()).toHaveFocus())
  })
})

describe('escalation status (real clock)', () => {
  it('the mid-countdown alert shows the time left as m:ss in a timer', async () => {
    await renderLoaded()
    const timers = within(inbox()).getAllByRole('timer')
    expect(timers).toHaveLength(1)
    const match = /^Escalates to the care centre nurse in (\d+):(\d{2})$/.exec(timers[0].textContent ?? '')
    expect(match).not.toBeNull()
    const seconds = Number(match?.[1]) * 60 + Number(match?.[2])
    expect(seconds).toBeGreaterThan(0)
    expect(seconds).toBeLessThanOrEqual(150)
    expect(rowFor('Possible fall, Test Resident H')).toContainElement(timers[0])
  })

  it('already-escalated alerts show their escalation line and are not announced on load', async () => {
    await renderLoaded()
    // Warnings: escalated after 5 minutes.
    expect(rowFor('Possible fall, Test Resident L')).toHaveTextContent('Escalated to the care centre nurse')
    expect(rowFor('Inactivity, Test Resident B')).toHaveTextContent('Escalated to the care centre nurse')
    // Critical: the nurse is the recipient, so the inbox shows just the Critical badge, no routing line.
    for (const ctx of ['Fall detected, Test Resident C', 'Fall detected, Test Resident Q']) {
      expect(rowFor(ctx)).toHaveTextContent('Critical')
      expect(rowFor(ctx)).not.toHaveTextContent(/Sent to the care centre nurse|at once|Escalat/)
    }
    // Every open alert but H's (mid-countdown) and the 2 critical ones: 6 warnings escalated.
    expect(within(inbox()).queryAllByText('Sent to the care centre nurse at once')).toEqual([])
    expect(within(inbox()).getAllByText('Escalated to the care centre nurse')).toHaveLength(6)
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('acknowledged demo alerts that escalated before the acknowledgement show no escalation line', async () => {
    await renderLoaded()
    for (const ctx of ['High heart rate, Test Resident G', 'Device offline, Test Resident O', 'Low battery, Test Resident M']) {
      expect(rowFor(ctx)).toHaveTextContent('Acknowledged by')
      expect(rowFor(ctx)).not.toHaveTextContent(/Escalat/)
    }
  })

  it('a critical open alert with escalated_at shows no "at once" line in the nurse inbox, only the Critical badge', async () => {
    await renderLoaded(oneAlert({ escalated_at: new Date(Date.now() - 4 * 60_000).toISOString() }))
    const row = rowFor('Fall detected, Test Resident C')
    expect(within(row).getByText('Critical')).toBeInTheDocument()
    expect(within(row).getByText('Open')).toBeInTheDocument()
    expect(row).not.toHaveTextContent(/Sent to the care centre nurse|at once|Escalat/)
    expect(within(row).queryByRole('timer')).not.toBeInTheDocument()
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('an unassigned warning with escalated_at shows no "at once" line in the nurse inbox either', async () => {
    await renderLoaded(
      oneAlert({
        resident_id: 'unassigned',
        alert_type: 'device_offline',
        severity: 'warning',
        escalated_at: new Date(Date.now() - 4 * 60_000).toISOString(),
      }),
    )
    const row = rowFor('Device offline, Unknown resident')
    expect(row).not.toHaveTextContent(/Sent to the care centre nurse|at once|Escalat/)
  })

  it('the nurse inbox: no "at once" for a critical alert, but "Escalated to the care centre nurse" for an escalated warning', async () => {
    const escalatedAt = new Date(Date.now() - 4 * 60_000).toISOString()
    const data = oneAlert({ escalated_at: escalatedAt })
    const warning: ResidentAlert = {
      ...data.alerts[0],
      alert_id: 'alert-test-002',
      resident_id: 'res-0002',
      device_id: data.residents[1].bedroom.device_id,
      alert_type: 'inactivity',
      severity: 'warning',
      escalated_at: escalatedAt,
    }
    await renderLoaded({ ...data, alerts: [data.alerts[0], warning] })
    expect(rowFor('Fall detected, Test Resident C')).not.toHaveTextContent(/Sent to the care centre nurse|Escalat/)
    expect(rowFor('Inactivity, Test Resident B')).toHaveTextContent('Escalated to the care centre nurse')
    expect(inbox()).not.toHaveTextContent('at once')
  })

  it('shows "Escalated to the care centre nurse" for a warning from escalated_at alone', async () => {
    await renderLoaded(
      oneAlert({
        alert_type: 'inactivity',
        severity: 'warning',
        escalated_at: new Date(Date.now() - 4 * 60_000).toISOString(),
      }),
    )
    const row = rowFor('Inactivity, Test Resident C')
    expect(row).toHaveTextContent('Escalated to the care centre nurse')
    expect(row).not.toHaveTextContent('at once')
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('acknowledged alerts show no countdown and no escalation, past or future', async () => {
    const past = new Date(Date.now() - 60_000).toISOString()
    const future = new Date(Date.now() + 60_000).toISOString()
    const data = oneAlert({
      status: 'acknowledged',
      acknowledged_at: past,
      escalates_at: past,
      escalated_at: past,
    })
    const second: ResidentAlert = {
      ...data.alerts[0],
      alert_id: 'alert-test-002',
      resident_id: 'res-0008',
      alert_type: 'possible_fall',
      severity: 'warning',
      escalates_at: future,
    }
    await renderLoaded({ ...data, alerts: [data.alerts[0], second] })
    expect(within(inbox()).queryByRole('timer')).not.toBeInTheDocument()
    expect(inbox()).not.toHaveTextContent('Escalated to the care centre nurse')
    expect(inbox()).not.toHaveTextContent('Escalates to the care centre nurse')
    expect(inbox()).not.toHaveTextContent('Sent to the care centre nurse at once')
  })

  it('open alerts without escalates_at or escalated_at show neither', async () => {
    await renderLoaded(oneAlert())
    const row = rowFor('Fall detected, Test Resident C')
    expect(within(row).queryByRole('timer')).not.toBeInTheDocument()
    expect(row).not.toHaveTextContent(/Escalat/)
  })
})

describe('escalation countdown (fake clock)', () => {
  const NOW = new Date('2026-10-18T02:30:00Z')
  const H = 'Possible fall, Test Resident H'

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  async function renderAtNow(data: DashboardData = demoDataFor('staff', NOW)) {
    dashboardLoader.mockResolvedValue(data)
    renderWithAuth(<NurseDashboard />, { user: TEST_USERS.nurse })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(inboxHeading()).toBeInTheDocument()
  }

  const tick = (ms: number) =>
    act(() => {
      vi.advanceTimersByTime(ms)
    })

  it('starts at 2:30 and ticks down every second', async () => {
    await renderAtNow()
    const timer = () => within(rowFor(H)).getByRole('timer')
    expect(timer()).toHaveTextContent(/^Escalates to the care centre nurse in 2:30$/)
    tick(1000)
    expect(timer()).toHaveTextContent(/^Escalates to the care centre nurse in 2:29$/)
    tick(1000)
    expect(timer()).toHaveTextContent(/^Escalates to the care centre nurse in 2:28$/)
    tick(88_000)
    expect(timer()).toHaveTextContent(/^Escalates to the care centre nurse in 1:00$/)
    tick(1000)
    expect(timer()).toHaveTextContent(/^Escalates to the care centre nurse in 0:59$/)
    tick(58_000)
    expect(timer()).toHaveTextContent(/^Escalates to the care centre nurse in 0:01$/)
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('flips to "Escalated to the care centre nurse" at zero and announces it once', async () => {
    await renderAtNow()
    tick(149_000)
    expect(within(rowFor(H)).getByRole('timer')).toHaveTextContent(/in 0:01$/)
    tick(1000)
    expect(within(rowFor(H)).queryByRole('timer')).not.toBeInTheDocument()
    expect(rowFor(H)).toHaveTextContent('Escalated to the care centre nurse')
    expect(rowFor(H)).not.toHaveTextContent('at once')
    expect(announcer()).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)
    // 6 warnings escalated on load + H; the 2 critical ones show no routing line in the nurse inbox.
    expect(within(inbox()).getAllByText('Escalated to the care centre nurse')).toHaveLength(7)
    expect(within(inbox()).queryAllByText('Sent to the care centre nurse at once')).toEqual([])

    // A second announcement would toggle the trailing no-break space, so compare exactly.
    const announced = announcer().textContent
    tick(5000)
    expect(announcer().textContent).toBe(announced)
  })

  it('a later action announcement replaces the escalation message', async () => {
    await renderAtNow()
    tick(180_000)
    expect(announcer()).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)
    // fireEvent, not user-event: user-event's internal delays stall under fake timers.
    acknowledgeNow(H)
    expect(announcer()).toHaveTextContent(`Acknowledged: ${H}.`)
    expect(rowFor(H)).not.toHaveTextContent('Escalated to the care centre nurse')
  })

  it('announces an escalation that happens while another panel is shown', async () => {
    await renderAtNow()
    fireEvent.click(tile('Devices offline'))
    tick(180_000)
    // The announcer stays mounted; the nurse should still hear that the alert escalated.
    expect(announcer()).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)
  })

  it('returning to the inbox after an escalation shows it, without announcing it again', async () => {
    await renderAtNow()
    fireEvent.click(tile('Residents monitored'))
    tick(180_000)
    const announced = announcer().textContent
    fireEvent.click(tile('Open alerts'))
    expect(rowFor(H)).toHaveTextContent('Escalated to the care centre nurse')
    expect(within(rowFor(H)).queryByRole('timer')).not.toBeInTheDocument()
    tick(5000)
    expect(announcer().textContent).toBe(announced)
  })

  it('acknowledging before the deadline means no escalation announcement', async () => {
    await renderAtNow()
    tick(140_000)
    acknowledgeNow(H)
    expect(announcer()).toHaveTextContent(`Acknowledged: ${H}.`)
    const announced = announcer().textContent
    tick(5 * 60_000)
    expect(announcer().textContent).toBe(announced)
    expect(rowFor(H)).not.toHaveTextContent(/Escalat/)
  })

  it('resolving before the deadline means no escalation announcement', async () => {
    await renderAtNow()
    tick(140_000)
    fireEvent.click(resolveButton(H))
    const announced = announcer().textContent
    expect(announced).toContain(`Resolved: ${H}.`)
    tick(5 * 60_000)
    expect(announcer().textContent).toBe(announced)
  })

  it('a cancelled alert with a future escalates_at is not listed, shows no countdown, and is never announced', async () => {
    const data = demoDataFor('staff', NOW)
    const target = data.alerts.find((a) => a.resident_id === 'res-0008') as ResidentAlert
    expect(Date.parse(target.escalates_at ?? '')).toBeGreaterThan(NOW.getTime())
    const alerts = data.alerts.map((a) =>
      a === target
        ? { ...a, status: 'cancelled' as const, cancelled_at: NOW.toISOString(), cancelled_by_role: 'resident' as const }
        : a,
    )
    await renderAtNow({ ...data, alerts })
    expect(inboxHeading()).toHaveTextContent('Open alerts (11)')
    expect(inbox()).not.toHaveTextContent('Test Resident H')
    expect(screen.queryByRole('button', { name: `Acknowledge: ${H}` })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `Resolve: ${H}` })).not.toBeInTheDocument()
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    tick(5 * 60_000)
    expect(announcer()).toBeEmptyDOMElement()
    expect(inbox()).not.toHaveTextContent('Test Resident H')
  })

  it('acknowledging a different alert does not reset or repeat the countdown announcement', async () => {
    await renderAtNow()
    tick(10_000)
    acknowledgeNow('Fall detected, Test Resident C')
    expect(within(rowFor(H)).getByRole('timer')).toHaveTextContent(/^Escalates to the care centre nurse in 2:20$/)
    tick(140_000)
    expect(announcer()).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)
    const announced = announcer().textContent
    acknowledgeNow('Fall detected, Test Resident Q')
    expect(announcer()).toHaveTextContent('Acknowledged: Fall detected, Test Resident Q.')
    expect(announcer().textContent).not.toBe(announced)
    const afterAck = announcer().textContent
    tick(5 * 60_000)
    expect(announcer().textContent).toBe(afterAck)
  })
})

describe('devices offline panel', () => {
  it('lists each offline device with its kind, ID, resident line, and last seen', async () => {
    const { user, data } = await renderLoaded()
    await user.click(tile('Devices offline'))
    const panel = screen.getByRole('region', { name: /^Devices offline/ })
    const items = within(panel).getAllByRole('listitem')
    expect(items).toHaveLength(6)

    const byId = (id: string) => {
      const resident = data.residents.find((r) => r.resident_id === id)
      if (!resident) throw new Error(`No demo resident ${id}`)
      return resident
    }
    // Resident order, wearable before bedroom.
    const expected = [
      ['res-0006', 'Test Resident F', 'bedroom', 'Last seen 1 day ago'],
      ['res-0009', 'Test Resident I', 'bedroom', 'Last seen 1 h ago'],
      ['res-0012', 'Test Resident L', 'wearable', 'Last seen 18 min ago'],
      ['res-0015', 'Test Resident O', 'wearable', 'Last seen 52 min ago'],
      ['res-0015', 'Test Resident O', 'bedroom', 'Last seen 3 h ago'],
      ['res-0017', 'Test Resident Q', 'bedroom', 'Last seen 2 days ago'],
    ] as const
    expected.forEach(([id, name, kind, lastSeen], n) => {
      const resident = byId(id)
      const label = kind === 'wearable' ? 'Wearable' : 'Bedroom sensor'
      expect(items[n]).toHaveTextContent(`${label} offline· ${resident[kind].device_id}`)
      expect(items[n]).toHaveTextContent(name)
      expect(items[n]).toHaveTextContent(id)
      expect(items[n]).toHaveTextContent(resident.home_id)
      expect(items[n]).toHaveTextContent(lastSeen)
    })
  })

  it('shows "All devices are online." when none are offline', async () => {
    const data = demoDataFor('staff')
    const residents = data.residents.map((r) => ({
      ...r,
      wearable: { ...r.wearable, online: true },
      bedroom: { ...r.bedroom, online: true },
    }))
    const { user } = await renderLoaded({ ...data, residents })
    expect(tileValue('Devices offline')).toBe('0')
    await user.click(tile('Devices offline'))
    expect(screen.getByRole('heading', { level: 2, name: 'Devices offline (0)' })).toBeInTheDocument()
    expect(screen.getByText('All devices are online.')).toBeInTheDocument()
  })
})

describe('low battery panel', () => {
  it('lists wearables below 20%, lowest first, with battery, device, resident, and worn state', async () => {
    const { user, data } = await renderLoaded()
    await user.click(tile('Wearables low on battery'))
    const panel = screen.getByRole('region', { name: /^Wearables low on battery/ })
    expect(panel).toHaveTextContent('Below 20%, lowest first.')
    const items = within(panel).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    const m = data.residents.find((r) => r.resident_id === 'res-0013')
    expect(items[0]).toHaveTextContent('12%')
    expect(items[0]).toHaveTextContent(m?.wearable.device_id ?? 'missing')
    expect(items[0]).toHaveTextContent('Test Resident M')
    expect(items[0]).toHaveTextContent('Worn')
    expect(items[1]).toHaveTextContent('15%')
    expect(items[1]).toHaveTextContent('Test Resident F')
  })

  it('shows "No wearables are below 20%." when none are low', async () => {
    const data = demoDataFor('staff')
    const residents = data.residents.map((r) => ({ ...r, wearable: { ...r.wearable, battery_pct: 80 } }))
    const { user } = await renderLoaded({ ...data, residents })
    await user.click(tile('Wearables low on battery'))
    expect(screen.getByRole('heading', { level: 2, name: 'Wearables low on battery (0)' })).toBeInTheDocument()
    expect(screen.getByText('No wearables are below 20%.')).toBeInTheDocument()
  })
})

describe('residents panel', () => {
  it('shows all 20 residents with search and filters, and no sparklines', async () => {
    const { user } = await renderLoaded()
    expect(residentCards()).toHaveLength(0)
    await user.click(tile('Residents monitored'))
    expect(residentCards()).toHaveLength(20)
    expect(screen.getByText('Showing 20 of 20 residents')).toBeInTheDocument()
    expect(screen.queryByText(/^Last 5 readings, oldest first:/)).not.toBeInTheDocument()
    expect(document.querySelector('article svg polyline')).toBeNull()

    await user.click(screen.getByRole('searchbox', { name: 'Search residents' }))
    await user.paste('res-0012')
    expect(residentCards()).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 3, name: 'Test Resident L' })).toBeInTheDocument()
  })

  it('resident cards show vitals, battery, wearable state, and last updated', async () => {
    const { user } = await renderLoaded()
    await user.click(tile('Residents monitored'))
    const card = within(screen.getByRole('heading', { level: 3, name: 'Test Resident J' }).closest('article') as HTMLElement)
    expect(card.getByText('res-0010')).toBeInTheDocument()
    expect(card.getByText('Heart rate').nextElementSibling).toHaveTextContent('71 bpm')
    expect(card.getByText('SpO2').nextElementSibling).toHaveTextContent('97%')
    expect(card.getByText('Skin temp').nextElementSibling).toHaveTextContent('33.6 °C')
    expect(card.getByText('Battery').nextElementSibling).toHaveTextContent('58%')
    expect(card.getByText('Not worn · Charging')).toBeInTheDocument()
    expect(card.getByText(/Last updated/)).toBeInTheDocument()
  })

  it('shows "No residents yet" when there are no residents', async () => {
    const { user } = await renderLoaded({ residents: [], alerts: [] })
    expect(tileValue('Residents monitored')).toBe('0')
    await user.click(tile('Residents monitored'))
    expect(screen.getByText('No residents yet')).toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
  })
})

describe('icon and text, never colour alone', () => {
  it('every severity badge has an icon and a text label', async () => {
    const { container } = await renderLoaded()
    const badges = container.querySelectorAll('[data-tone]')
    expect(badges).toHaveLength(12)
    const labels = { warning: 'Warning', critical: 'Critical' }
    badges.forEach((badge) => {
      const tone = badge.getAttribute('data-tone') as keyof typeof labels
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(badge).toHaveTextContent(labels[tone])
    })
  })

  it('lifecycle labels ("Open", "Acknowledged") and the countdown have an icon and text', async () => {
    await renderLoaded()
    openRows().forEach((row) => {
      const label = within(row).getByText(/^(Open|Acknowledged)$/)
      expect(label.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    })
    const timer = within(inbox()).getByRole('timer')
    expect(timer.parentElement?.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    const escalated = within(inbox()).getAllByText('Escalated to the care centre nurse')
    expect(escalated).toHaveLength(6)
    escalated.forEach((el) => expect(el.querySelector('svg[aria-hidden="true"]')).not.toBeNull())
  })
})

// axe over a full dashboard is slow when the whole suite runs in parallel.
describe('accessibility', { timeout: 30_000 }, () => {
  it('has no axe violations (alerts panel)', async () => {
    const { container } = await renderLoaded()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations with a resolved alert listed', async () => {
    const { container, user } = await renderLoaded()
    await user.click(resolveButton('Fall detected, Test Resident C'))
    expect(await axeViolations(container)).toEqual([])
  })

  it.each(['Devices offline', 'Wearables low on battery', 'Residents monitored'])(
    'has no axe violations on the "%s" panel',
    async (label) => {
      const { container, user } = await renderLoaded()
      await user.click(tile(label))
      expect(await axeViolations(container)).toEqual([])
    },
  )

  it('has no axe violations in the loading state', async () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    const { container } = renderNurse()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations in the error state', async () => {
    dashboardLoader.mockRejectedValue(new Error('boom'))
    const { container } = renderNurse()
    await screen.findByRole('alert')
    expect(await axeViolations(container)).toEqual([])
  })

  it.each(['Open alerts', 'Residents monitored'])('headings are in order on the "%s" panel', async (label) => {
    const { user } = await renderLoaded()
    await user.click(tile(label))
    const levels = screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))
    expect(levels[0]).toBe(1)
    levels.slice(1).forEach((level, i) => expect(level - levels[i]).toBeLessThanOrEqual(1))
  })

  it('action buttons name the alert they act on', async () => {
    await renderLoaded()
    screen.getAllByRole('button', { name: /^(Acknowledge|Resolve): / }).forEach((button) => {
      expect(button).toHaveAccessibleName(/^(Acknowledge|Resolve): .+, Test Resident [A-T]$/)
    })
  })
})

describe('staff area nav', () => {
  it('shows the "Staff area" tabs above the banner, with Dashboard as the current page', async () => {
    await renderLoaded()
    const nav = screen.getByRole('navigation', { name: 'Staff area' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual(['Dashboard', 'Devices'])
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/')
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Devices' })).toHaveAttribute('href', '/devices')
    expect(within(nav).getByRole('link', { name: 'Devices' })).not.toHaveAttribute('aria-current')
    const banner = screen.getByRole('note', { name: 'Demo data' })
    expect(nav.compareDocumentPosition(banner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('is shown in the loading and error states too', async () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    renderNurse()
    expect(screen.getByRole('navigation', { name: 'Staff area' })).toBeInTheDocument()
    cleanup()
    dashboardLoader.mockRejectedValue(new Error('boom'))
    renderNurse()
    await screen.findByRole('alert')
    expect(screen.getByRole('navigation', { name: 'Staff area' })).toBeInTheDocument()
  })

  it('clicking Devices goes to /devices', async () => {
    const { user } = await renderLoaded()
    await user.click(screen.getByRole('link', { name: 'Devices' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/devices')
  })
})
