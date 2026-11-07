import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiAlert, apiResident } from '../../test/apiDashboard'
import { axeViolations } from '../../test/axe'
import { dashboardLoader, demoDataFor, resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { AuthUser } from '../../types/auth'
import type {
  ApiDashboardAlert,
  ApiDashboardResident,
  DashboardData,
  ResidentAlert,
} from '../../types/dashboard'
import type { DemoDashboardData } from './demoData'
import { NurseDashboard } from './NurseDashboard'
import { toResident, toResidentAlert, type DashboardView } from './useDashboardData'

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

async function renderLoaded(data: DemoDashboardData = demoDataFor('staff'), user?: AuthUser) {
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
/** Clicks Acknowledge twice (opens the note form, submits it), then lets the action's
 * promise settle: the announcement comes only once the change is accepted. */
async function acknowledgeNow(context: string) {
  fireEvent.click(ackButton(context))
  fireEvent.click(ackButton(context))
  await act(async () => {})
}
const resolveButton = (context: string) => screen.getByRole('button', { name: `Resolve: ${context}` })
const RESOLVE_NOTE = 'Synthetic note: checked, false alarm.'
const resolveField = () => screen.getByRole('textbox', { name: 'Closing note (required)' })
/**
 * Resolve opens the closing-note form (the note is required); its submit button has the
 * same accessible name, so: open, type a note, submit.
 */
async function resolve(user: ReturnType<typeof userEvent.setup>, context: string, note = RESOLVE_NOTE) {
  await user.click(resolveButton(context))
  await user.type(resolveField(), note)
  await user.click(resolveButton(context))
}
/** As `resolve`, with fireEvent (for fake timers), then lets the action's promise settle. */
async function resolveNow(context: string, note = RESOLVE_NOTE) {
  fireEvent.click(resolveButton(context))
  fireEvent.change(resolveField(), { target: { value: note } })
  fireEvent.click(resolveButton(context))
  await act(async () => {})
}
/**
 * The one alert row (open list or resolved list) for "<type>, <resident>", found by its
 * type line and resident name, not by a button: device-problem rows have no buttons.
 */
const rowFor = (context: string) => {
  const [type, resident] = context.split(', ')
  const rows = within(inbox())
    .getAllByRole('listitem')
    .filter((li) => within(li).queryByText(type, { selector: 'p' }) !== null && (li.textContent ?? '').includes(resident))
  if (rows.length !== 1) throw new Error(`Expected one row for "${context}", found ${rows.length}`)
  return rows[0]
}
const residentCards = () => screen.queryAllByRole('article')

/** Demo inbox order: critical first (newest first), then the rest newest first. */
const DEMO_INBOX_ORDER: ReadonlyArray<[string, string]> = [
  ['Fall detected', 'Test Resident C'],
  ['High heart rate', 'Test Resident G'],
  // Possible falls are always critical (docs/features/possible-fall.md), so pinned too.
  ['Possible fall', 'Test Resident L'],
  ['Fall detected', 'Test Resident Q'],
  ['Possible fall', 'Test Resident B'],
  // B's warning for the caregiver view's demo countdown (2 min old), then H (2.5 min).
  ['Inactivity', 'Test Resident B'],
  ['Low blood oxygen', 'Test Resident H'],
  ['Low blood oxygen', 'Test Resident S'],
  ['Wearable not worn', 'Test Resident B'],
  ['Device offline', 'Test Resident O'],
  ['Low battery', 'Test Resident F'],
  ['Device offline', 'Test Resident I'],
  ['Low battery', 'Test Resident M'],
]

/** A single synthetic alert for focused tests. */
function oneAlert(overrides: Partial<ResidentAlert> = {}): DemoDashboardData {
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
    dashboardLoader.mockReturnValue(deferred<DemoDashboardData>().promise)
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
    // 13 demo alerts, all unresolved; 5 critical (2 falls, 1 acknowledged vitals, 2 possible falls).
    expect(tileValue('Residents monitored')).toBe('20')
    expect(tileValue('Open alerts')).toBe('13')
    expect(within(tile('Open alerts')).getByText('5 critical')).toBeInTheDocument()
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
    expect(inboxHeading()).toHaveTextContent('Open alerts (13)')
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

    // 10 open + 3 acknowledged in the demo data. Acknowledge only on the 7 open health
    // alerts: the 3 open device problems are acknowledged by tech staff or caregivers.
    expect(screen.getAllByRole('button', { name: /^Acknowledge: / })).toHaveLength(7)
    // Resolve on the 8 health alerts: device problems are resolved only by the watchdog.
    expect(screen.getAllByRole('button', { name: /^Resolve: / })).toHaveLength(8)
  })

  it('device problems have no Acknowledge and no Resolve (user decisions 2026-11-05)', async () => {
    await renderLoaded()
    for (const ctx of [
      'Wearable not worn, Test Resident B',
      'Low battery, Test Resident F',
      'Device offline, Test Resident I',
      'Device offline, Test Resident O',
      'Low battery, Test Resident M',
    ]) {
      expect(within(rowFor(ctx)).getByText(/^(Open|Acknowledged)$/)).toBeInTheDocument()
      expect(within(rowFor(ctx)).queryAllByRole('button')).toEqual([])
    }
  })

  it('a device problem a caregiver acknowledged reads "Acknowledged by <name> (caregiver)", with no note', async () => {
    await renderLoaded()
    const o = rowFor('Device offline, Test Resident O')
    expect(o).toHaveTextContent(/Acknowledged by Test Caregiver B \(caregiver\), 40 min ago/)
    expect(o).not.toHaveTextContent(/care centre nurse|Message from the care centre/)
    expect(rowFor('Low battery, Test Resident M')).toHaveTextContent(/Acknowledged by Test Caregiver A \(caregiver\)/)
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
    expect(openRows()).toHaveLength(11)
    openRows().forEach((row, i) => {
      expect(row).toHaveTextContent(expected[i][0])
      expect(row).toHaveTextContent(expected[i][1])
    })
    expect(inbox()).not.toHaveTextContent('Fall detected')
    expect(inbox()).not.toHaveTextContent('Cancelled')
    expect(inboxHeading()).toHaveTextContent('Open alerts (11)')
    expect(tileValue('Open alerts')).toBe('11')
    // Still critical: G's acknowledged High heart rate and the possible falls of L and B.
    expect(within(tile('Open alerts')).getByText('3 critical')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /: Fall detected, Test Resident (C|Q)$/ })).not.toBeInTheDocument()
    // 8 open + 3 acknowledged left; Acknowledge on the 5 open health alerts among them.
    expect(screen.getAllByRole('button', { name: /^Acknowledge: / })).toHaveLength(5)
    // Resolve on the 6 health alerts left (not on the 5 device problems).
    expect(screen.getAllByRole('button', { name: /^Resolve: / })).toHaveLength(6)
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
    const alerts = data.alerts.map((a): ResidentAlert =>
      a.resident_id === 'res-0008'
        ? { ...a, status: 'resolved', resolved_by: 'Test Nurse C', resolved_at: new Date().toISOString() }
        : a,
    )
    await renderLoaded({ ...data, alerts })
    expect(openRows()).toHaveLength(12)
    expect(tileValue('Open alerts')).toBe('12')
    expect(screen.queryByRole('button', { name: /Resolve: Low blood oxygen, Test Resident H/ })).not.toBeInTheDocument()
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
    expect(openRows()).toHaveLength(13)
    expect(tileValue('Open alerts')).toBe('13')
    expect(openRows()[0]).toBe(rowFor(C)) // still pinned first
  })

  it('removes the "Not yet escalated" label', async () => {
    const { user } = await renderLoaded()
    const H = 'Low blood oxygen, Test Resident H'
    expect(within(rowFor(H)).getByText('Not yet escalated')).toBeInTheDocument()
    await acknowledge(user, H)
    expect(rowFor(H)).not.toHaveTextContent(/Not yet escalated|Escalat/)
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
    // Device problems are acknowledged by a caregiver, never with a nurse's note.
    expect(rowFor('Device offline, Test Resident O')).not.toHaveTextContent('Message from the care centre')
    expect(rowFor('Low battery, Test Resident M')).not.toHaveTextContent('Message from the care centre')
  })
})

describe('resolve', () => {
  const C = 'Fall detected, Test Resident C'

  it('removes the row and updates the heading count and the tiles', async () => {
    const { user } = await renderLoaded()
    await resolve(user, C)
    expect(screen.queryByRole('button', { name: `Resolve: ${C}` })).not.toBeInTheDocument()
    expect(openRows()).toHaveLength(12)
    expect(inboxHeading()).toHaveTextContent('Open alerts (12)')
    expect(tileValue('Open alerts')).toBe('12')
    expect(within(tile('Open alerts')).getByText('4 critical')).toBeInTheDocument()
  })

  it('lists it under "Resolved since this page opened" with "Resolved by Test Nurse A"', async () => {
    const { user } = await renderLoaded()
    expect(screen.queryByText(/Resolved since this page opened/)).not.toBeInTheDocument()
    await resolve(user, C)
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
    await resolve(user, C)
    await waitFor(() => expect(inboxHeading()).toHaveFocus())
    expect(inboxHeading()).toHaveAttribute('tabindex', '-1')
  })

  it('announces the resolution', async () => {
    const { user } = await renderLoaded()
    await resolve(user, C)
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${C}.`))
  })

  it('announcing the same text twice still changes the live region', async () => {
    // Two synthetic alerts with the same type and resident give the same "Resolved: ..." text.
    const data = oneAlert()
    const twin: ResidentAlert = { ...data.alerts[0], alert_id: 'alert-test-002' }
    const { user } = await renderLoaded({ ...data, alerts: [data.alerts[0], twin] })
    const firstRow = openRows()[0]
    await user.click(within(firstRow).getByRole('button', { name: `Resolve: ${C}` }))
    await user.type(within(firstRow).getByRole('textbox', { name: 'Closing note (required)' }), RESOLVE_NOTE)
    await user.click(within(firstRow).getByRole('button', { name: `Resolve: ${C}` }))
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${C}.`))
    const first = announcer().textContent
    await resolve(user, C)
    await waitFor(() => expect(announcer().textContent).not.toBe(first))
    expect(announcer()).toHaveTextContent(`Resolved: ${C}.`)
    expect(screen.getByText('Resolved since this page opened (2)')).toBeInTheDocument()
  })

  it('can resolve an acknowledged alert', async () => {
    const { user } = await renderLoaded()
    const G = 'High heart rate, Test Resident G'
    await resolve(user, G)
    expect(screen.queryByRole('button', { name: `Resolve: ${G}` })).not.toBeInTheDocument()
    const details = screen.getByText('Resolved since this page opened (1)').closest('details') as HTMLElement
    expect(details).toHaveTextContent(G)
  })

  it('acknowledge then resolve, newest resolution listed first', async () => {
    const { user } = await renderLoaded()
    await acknowledge(user, C)
    await resolve(user, C)
    await resolve(user, 'Low blood oxygen, Test Resident H')
    const details = screen.getByText('Resolved since this page opened (2)').closest('details') as HTMLElement
    const items = within(details).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    // Both resolved within the same second may share a timestamp, so only check both are present.
    expect(details).toHaveTextContent(C)
    expect(details).toHaveTextContent('Low blood oxygen, Test Resident H')
  })

  it('resolving the last alert shows "No open alerts."', async () => {
    const { user } = await renderLoaded(oneAlert())
    await resolve(user, C)
    expect(within(inbox()).getByText('No open alerts.')).toBeInTheDocument()
    expect(tileValue('Open alerts')).toBe('0')
    expect(screen.getByText('Resolved since this page opened (1)')).toBeInTheDocument()
    await waitFor(() => expect(inboxHeading()).toHaveFocus())
  })

  it('Resolve only opens the closing-note form: nothing changes or is announced until it is submitted', async () => {
    const { user } = await renderLoaded()
    await user.click(resolveButton(C))
    expect(screen.getByRole('form', { name: `Resolve: ${C}` })).toBeInTheDocument()
    await waitFor(() => expect(resolveField()).toHaveFocus())
    expect(openRows()).toHaveLength(13)
    expect(within(rowFor(C)).getByText('Open')).toBeInTheDocument()
    expect(announcer()).toBeEmptyDOMElement()
    expect(screen.queryByText(/Resolved since this page opened/)).not.toBeInTheDocument()
  })

  it('submitting without a note shows the error, resolves nothing, and announces nothing', async () => {
    const { user } = await renderLoaded()
    await user.click(resolveButton(C))
    await user.type(resolveField(), '   ')
    await user.click(resolveButton(C))
    expect(screen.getByText('Add a note saying how this alert was dealt with.')).toBeInTheDocument()
    expect(resolveField()).toHaveAttribute('aria-invalid', 'true')
    expect(openRows()).toHaveLength(13)
    expect(announcer()).toBeEmptyDOMElement()
    await waitFor(() => expect(resolveField()).toHaveFocus())
  })

  it('cancelling the form leaves the alert open, announces nothing, and returns focus to Resolve', async () => {
    const { user } = await renderLoaded()
    await user.click(resolveButton(C))
    await user.type(resolveField(), 'Synthetic draft')
    await user.click(screen.getByRole('button', { name: `Cancel, don't resolve: ${C}` }))
    expect(screen.queryByRole('form', { name: `Resolve: ${C}` })).not.toBeInTheDocument()
    expect(within(rowFor(C)).getByText('Open')).toBeInTheDocument()
    await waitFor(() => expect(resolveButton(C)).toHaveFocus())
    expect(announcer()).toBeEmptyDOMElement()
    expect(document.body).not.toHaveTextContent('Synthetic draft')
  })

  it('never announces the closing note, and does not show it in the resolved list', async () => {
    const { user } = await renderLoaded()
    await resolve(user, C, 'Synthetic note: ambulance called, resident taken to hospital.')
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${C}.`))
    expect(announcer()).not.toHaveTextContent('Synthetic note')
    expect(document.body).not.toHaveTextContent('ambulance called')
  })

  it('opening Resolve on one alert and then another leaves one form open per row, each acting on its own alert', async () => {
    const { user } = await renderLoaded()
    const Q = 'Fall detected, Test Resident Q'
    await user.click(resolveButton(C))
    await user.click(resolveButton(Q))
    expect(screen.getAllByRole('form')).toHaveLength(2)
    await user.type(within(rowFor(Q)).getByRole('textbox', { name: 'Closing note (required)' }), RESOLVE_NOTE)
    await user.click(within(rowFor(Q)).getByRole('button', { name: `Resolve: ${Q}` }))
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${Q}.`))
    expect(within(rowFor(C)).getByText('Open')).toBeInTheDocument()
    expect(screen.getByRole('form', { name: `Resolve: ${C}` })).toBeInTheDocument()
  })
})

describe('escalation status (real clock)', () => {
  it('the alert still waiting to escalate shows a quiet "Not yet escalated", with no timer', async () => {
    await renderLoaded()
    // The nurse is the recipient: the countdown is a deadline for the caregiver, not her.
    expect(within(inbox()).queryByRole('timer')).not.toBeInTheDocument()
    expect(inbox()).not.toHaveTextContent('Escalates to the care centre nurse')
    // H's warning, and B's (the caregiver view's demo countdown): one label each.
    const labels = within(inbox()).getAllByText('Not yet escalated')
    expect(labels).toHaveLength(2)
    expect(within(rowFor('Low blood oxygen, Test Resident H')).getByText('Not yet escalated')).toBeInTheDocument()
    // B's is the first warning, after the 5 pinned critical alerts.
    expect(openRows()[5]).toHaveTextContent('Inactivity')
    expect(openRows()[5]).toHaveTextContent('Test Resident B')
    expect(within(openRows()[5]).getByText('Not yet escalated')).toBeInTheDocument()
  })

  it('escalated alerts show no routing line in the nurse inbox, and nothing is announced on load', async () => {
    await renderLoaded()
    // The nurse is the recipient, so neither "Escalated to…" nor "Sent… at once" is shown.
    for (const ctx of [
      'Possible fall, Test Resident L',
      'Low blood oxygen, Test Resident S',
      'Fall detected, Test Resident C',
      'Fall detected, Test Resident Q',
    ]) {
      expect(rowFor(ctx)).not.toHaveTextContent(/Sent to the care centre nurse|at once|Escalat|Not yet escalated/)
    }
    expect(within(rowFor('Fall detected, Test Resident C')).getByText('Critical')).toBeInTheDocument()
    expect(inbox()).not.toHaveTextContent(/Escalated to the care centre nurse|at once/)
    // Device problems never go to the nurse (docs/features/watchdog.md): no routing line.
    expect(rowFor('Wearable not worn, Test Resident B')).not.toHaveTextContent(/Escalat|at once|Not yet escalated/)
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

  it('the nurse inbox: no routing line for a critical alert or for an escalated warning', async () => {
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
    expect(rowFor('Inactivity, Test Resident B')).not.toHaveTextContent(/Escalat|Not yet escalated/)
    expect(inbox()).not.toHaveTextContent('at once')
  })

  it('a warning with escalated_at alone shows no routing line and no "Not yet escalated"', async () => {
    await renderLoaded(
      oneAlert({
        alert_type: 'inactivity',
        severity: 'warning',
        escalated_at: new Date(Date.now() - 4 * 60_000).toISOString(),
      }),
    )
    const row = rowFor('Inactivity, Test Resident C')
    expect(row).not.toHaveTextContent(/Escalat|at once|Not yet escalated/)
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
      alert_type: 'spo2_low',
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
  const H = 'Low blood oxygen, Test Resident H'

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  /**
   * The demo data without B's pending warning (the caregiver view's demo countdown, 3:00
   * left), so these tests follow H's alone; the full set is tested below.
   */
  const withoutB = () => {
    const data = demoDataFor('staff', NOW)
    return { ...data, alerts: data.alerts.filter((a) => !(a.resident_id === 'res-0002' && a.escalates_at)) }
  }

  async function renderAtNow(data: DemoDashboardData = withoutB()) {
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

  it('"Not yet escalated" stays, with no timer, until the deadline', async () => {
    await renderAtNow()
    const label = () => within(rowFor(H)).queryByText('Not yet escalated')
    expect(label()).toBeInTheDocument()
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    tick(90_000)
    expect(label()).toBeInTheDocument()
    tick(59_000)
    expect(label()).toBeInTheDocument()
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('at the deadline the label goes away, nothing replaces it, and the escalation is announced once', async () => {
    await renderAtNow()
    tick(149_000)
    expect(within(rowFor(H)).getByText('Not yet escalated')).toBeInTheDocument()
    tick(1000)
    expect(rowFor(H)).not.toHaveTextContent(/Not yet escalated|Escalat|at once/)
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(announcer()).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)
    expect(within(inbox()).queryAllByText(/Escalated to the care centre nurse|at once/)).toEqual([])

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
    await acknowledgeNow(H)
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

  it('returning to the inbox after an escalation shows no label, without announcing it again', async () => {
    await renderAtNow()
    fireEvent.click(tile('Residents monitored'))
    tick(180_000)
    const announced = announcer().textContent
    fireEvent.click(tile('Open alerts'))
    expect(rowFor(H)).not.toHaveTextContent(/Not yet escalated|Escalat/)
    expect(within(rowFor(H)).queryByRole('timer')).not.toBeInTheDocument()
    tick(5000)
    expect(announcer().textContent).toBe(announced)
  })

  it('acknowledging before the deadline means no escalation announcement', async () => {
    await renderAtNow()
    tick(140_000)
    await acknowledgeNow(H)
    expect(announcer()).toHaveTextContent(`Acknowledged: ${H}.`)
    const announced = announcer().textContent
    tick(5 * 60_000)
    expect(announcer().textContent).toBe(announced)
    expect(rowFor(H)).not.toHaveTextContent(/Escalat/)
  })

  it('resolving before the deadline means no escalation announcement', async () => {
    await renderAtNow()
    tick(140_000)
    await resolveNow(H)
    const announced = announcer().textContent
    expect(announced).toContain(`Resolved: ${H}.`)
    tick(5 * 60_000)
    expect(announcer().textContent).toBe(announced)
  })

  it("with the full demo data, H escalates at 2:30 and B's warning 30 s later, each announced once", async () => {
    await renderAtNow(demoDataFor('staff', NOW))
    expect(within(inbox()).getAllByText('Not yet escalated')).toHaveLength(2)
    tick(150_000)
    expect(announcer()).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)
    expect(within(inbox()).getAllByText('Not yet escalated')).toHaveLength(1)
    tick(30_000)
    expect(announcer()).toHaveTextContent('Escalated to the care centre nurse: Inactivity, Test Resident B.')
    expect(within(inbox()).queryByText('Not yet escalated')).not.toBeInTheDocument()
    const announced = announcer().textContent
    tick(5 * 60_000)
    expect(announcer().textContent).toBe(announced)
  })

  it('a cancelled alert with a future escalates_at is not listed, shows no countdown, and is never announced', async () => {
    const data = withoutB()
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
    await acknowledgeNow('Fall detected, Test Resident C')
    expect(within(rowFor(H)).getByText('Not yet escalated')).toBeInTheDocument()
    tick(139_000)
    expect(within(rowFor(H)).getByText('Not yet escalated')).toBeInTheDocument()
    expect(announcer()).toHaveTextContent('Acknowledged: Fall detected, Test Resident C.')
    tick(1_000)
    expect(announcer()).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)
    const announced = announcer().textContent
    await acknowledgeNow('Fall detected, Test Resident Q')
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
    expect(badges).toHaveLength(13)
    const labels = { warning: 'Warning', critical: 'Critical' }
    badges.forEach((badge) => {
      const tone = badge.getAttribute('data-tone') as keyof typeof labels
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(badge).toHaveTextContent(labels[tone])
    })
  })

  it('lifecycle labels ("Open", "Acknowledged") and "Not yet escalated" have an icon and text', async () => {
    await renderLoaded()
    openRows().forEach((row) => {
      const label = within(row).getByText(/^(Open|Acknowledged)$/)
      expect(label.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    })
    const waiting = within(inbox()).getAllByText('Not yet escalated')
    expect(waiting).toHaveLength(2)
    waiting.forEach((el) => expect(el.querySelector('svg[aria-hidden="true"]')).not.toBeNull())
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
    await resolve(user, 'Fall detected, Test Resident C')
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
    dashboardLoader.mockReturnValue(deferred<DemoDashboardData>().promise)
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
    dashboardLoader.mockReturnValue(deferred<DemoDashboardData>().promise)
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

// ---------------------------------------------------------------------------
// GET /dashboard data: API-shaped bodies mapped through toResident / toResidentAlert,
// with the nulls the API sends (no device assigned, never reported, unknown battery).

describe('rendered from GET /dashboard data', () => {
  const minutesFromNow = (m: number) => new Date(Date.now() + m * 60_000).toISOString()

  /**
   * A: all normal. B: no wearable, no bedroom unit, no readings. C: wearable offline and
   * never reported. D: wearable at 12%, bedroom unit offline (last seen 30 min ago).
   * E: wearable with an unknown battery, bedroom unit offline and never reported.
   */
  function apiDashboardData(): DashboardData {
    const residents: ApiDashboardResident[] = [
      apiResident(1, { status: 'urgent', status_text: 'Synthetic: fall detected.' }),
      apiResident(2, { wearable: null, bedroom: null, vitals: [], home_id: null }),
      apiResident(3, { wearable: { ts: null, battery_pct: null, worn: null, online: false }, vitals: [] }),
      apiResident(4, { wearable: { battery_pct: 12 }, bedroom: { online: false, ts: minutesFromNow(-30) } }),
      apiResident(5, { wearable: { battery_pct: null }, bedroom: { online: false, ts: null } }),
    ]
    const alerts: ApiDashboardAlert[] = [
      apiAlert({
        alert_id: 'alert-test-001',
        resident_id: 'resident-test-001',
        device_id: 'wearable-test-001',
        alert_type: 'fall',
        severity: 'critical',
        event_ts: minutesFromNow(-1),
        escalated_at: minutesFromNow(-1),
      }),
      apiAlert({
        alert_id: 'alert-test-002',
        resident_id: 'resident-test-004',
        device_id: 'wearable-test-004',
        alert_type: 'spo2_low',
        severity: 'warning',
        event_ts: minutesFromNow(-2),
        escalates_at: minutesFromNow(3),
      }),
      apiAlert({
        alert_id: 'alert-test-003',
        resident_id: 'resident-test-004',
        device_id: 'wearable-test-004',
        alert_type: 'battery_low',
        severity: 'warning',
        status: 'acknowledged',
        event_ts: minutesFromNow(-20),
        acknowledged_at: minutesFromNow(-10),
        acknowledged_by_role: 'caregiver',
        acknowledged_by: 'Test Caregiver A',
      }),
    ]
    return { residents: residents.map(toResident), alerts: alerts.map(toResidentAlert) }
  }

  async function renderApiLoaded() {
    dashboardLoader.mockResolvedValue(apiDashboardData())
    const ctx = renderNurse()
    await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
    return ctx
  }

  it('tiles count only devices that exist and batteries that are known', async () => {
    await renderApiLoaded()
    expect(tileValue('Residents monitored')).toBe('5')
    expect(tileValue('Open alerts')).toBe('3')
    expect(within(tile('Open alerts')).getByText('1 critical')).toBeInTheDocument()
    // C's wearable, D's and E's bedroom units; B has no devices to be offline.
    expect(tileValue('Devices offline')).toBe('3')
    // D only: C and E have an unknown battery, B has no wearable.
    expect(tileValue('Wearables low on battery')).toBe('1')
  })

  it('shows the API alerts in the inbox, with the escalation label and the caregiver acknowledge', async () => {
    await renderApiLoaded()
    expect(inboxHeading()).toHaveTextContent('Open alerts (3)')
    expect(openRows()).toHaveLength(3)

    const fall = rowFor('Fall detected, Test Resident A')
    expect(within(fall).getByText('Open')).toBeInTheDocument()
    expect(ackButton('Fall detected, Test Resident A')).toBeInTheDocument()

    const spo2 = rowFor('Low blood oxygen, Test Resident D')
    expect(within(spo2).getByText('Not yet escalated')).toBeInTheDocument()
    expect(spo2.querySelector('[role="timer"]')).toBeNull()

    const battery = rowFor('Low battery, Test Resident D')
    expect(battery).toHaveTextContent(/Acknowledged by Test Caregiver A \(caregiver\), 10 min ago/)
    expect(battery).not.toHaveTextContent(/care centre nurse/)
    expect(within(battery).queryAllByRole('button')).toEqual([])
  })

  it('the nurse can acknowledge an alert mapped from the API', async () => {
    const { user } = await renderApiLoaded()
    await acknowledge(user, 'Fall detected, Test Resident A')
    const fall = rowFor('Fall detected, Test Resident A')
    expect(within(fall).getByText('Acknowledged')).toBeInTheDocument()
    expect(fall).toHaveTextContent(/Acknowledged by the care centre nurse/)
    await waitFor(() => expect(announcer()).toHaveTextContent('Acknowledged: Fall detected, Test Resident A.'))
  })

  it('offline panel: "Never reported" for a device with no ts, "Last seen" otherwise, nothing for no device', async () => {
    const { user } = await renderApiLoaded()
    await user.click(tile('Devices offline'))
    const panel = screen.getByRole('region', { name: /^Devices offline/ })
    const items = within(panel).getAllByRole('listitem')
    expect(items).toHaveLength(3)

    expect(items[0]).toHaveTextContent('wearable-test-003')
    expect(items[0]).toHaveTextContent('Test Resident C')
    expect(items[0]).toHaveTextContent('Never reported')
    expect(items[0]).not.toHaveTextContent('Last seen')

    expect(items[1]).toHaveTextContent('bedroom-test-004')
    expect(items[1]).toHaveTextContent('Test Resident D')
    expect(items[1]).toHaveTextContent('Last seen 30 min ago')

    expect(items[2]).toHaveTextContent('bedroom-test-005')
    expect(items[2]).toHaveTextContent('Test Resident E')
    expect(items[2]).toHaveTextContent('Never reported')

    expect(panel).not.toHaveTextContent(/Test Resident B|NaN|Invalid|undefined|null/)
  })

  it('low-battery panel leaves out unknown batteries and residents with no wearable', async () => {
    const { user } = await renderApiLoaded()
    await user.click(tile('Wearables low on battery'))
    const panel = screen.getByRole('region', { name: /^Wearables low on battery/ })
    const items = within(panel).getAllByRole('listitem')
    expect(items).toHaveLength(1)
    expect(items[0]).toHaveTextContent('12%')
    expect(items[0]).toHaveTextContent('wearable-test-004')
    expect(items[0]).toHaveTextContent('Test Resident D')
    expect(panel).not.toHaveTextContent(/Unknown|Test Resident [BCE]/)
  })

  it('residents panel: cards with no wearable, no bedroom unit, or never reported render without crashing', async () => {
    const { user, container } = await renderApiLoaded()
    await user.click(tile('Residents monitored'))
    const cards = residentCards()
    expect(cards).toHaveLength(5)
    const cardOf = (name: string) => {
      const card = cards.find((c) => within(c).queryByRole('heading', { level: 3, name }))
      if (!card) throw new Error(`No card for ${name}`)
      return card
    }
    const b = cardOf('Test Resident B')
    expect(b).toHaveTextContent('No wearable assigned')
    expect(b).toHaveTextContent('No readings yet')
    expect(b).toHaveTextContent('Unknown')
    expect(within(b).getByRole('link', { name: 'Test Resident B' })).toHaveAttribute('href', '/residents/resident-test-002')

    const c = cardOf('Test Resident C')
    expect(c).toHaveTextContent('Offline · Never reported')
    expect(c).toHaveTextContent('No readings yet')

    expect(cardOf('Test Resident E')).toHaveTextContent('Unknown')
    expect(cardOf('Test Resident D')).toHaveTextContent('12%')
    for (const card of cards) expect(card).not.toHaveTextContent(/NaN|Invalid date|undefined|null/)
    expect(await axeViolations(container)).toEqual([])
  })

  it('the open-alerts view of API data has no axe violations', async () => {
    const { container } = await renderApiLoaded()
    expect(await axeViolations(container)).toEqual([])
  })
})
