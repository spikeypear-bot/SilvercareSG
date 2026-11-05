import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { dashboardLoader, demoDataFor, resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import { CaregiverView } from './CaregiverView'
import { formatActivity, formatHr, formatSpo2, formatTemp } from './labels'
import type { DashboardView } from './useDashboardData'

vi.mock('./useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./useDashboardData')>()
  const { dashboardLoader } = await import('../../test/dashboardMock')
  return {
    ...actual,
    useDashboardData: (view: DashboardView) => actual.useDashboardData(view, dashboardLoader),
  }
})

beforeEach(() => {
  resetDashboardLoader()
})

function renderCaregiver() {
  const user = userEvent.setup()
  const view = renderWithAuth(<CaregiverView />, { user: TEST_USERS.caregiver })
  return { user, ...view }
}

async function renderLoaded(data: DashboardData = demoDataFor('family')) {
  dashboardLoader.mockResolvedValue(data)
  const ctx = renderCaregiver()
  await screen.findAllByRole('article')
  return { ...ctx, data }
}

// --- queries ---------------------------------------------------------------

const cardFor = (name: string) =>
  screen.getByRole('article', { name }) as HTMLElement
/** The "Recent alerts" live region inside one resident's card. */
const recentAlerts = (card: HTMLElement) =>
  within(card).getByRole('heading', { level: 3, name: 'Recent alerts' }).nextElementSibling as HTMLElement
const timelineItems = (card: HTMLElement) => {
  const list = recentAlerts(card).querySelector('ol')
  return list ? within(list as HTMLElement).getAllByRole('listitem') : []
}

/** A synthetic alert for one of the caregiver's linked residents (res-0002 or res-0005). */
function alertFor(data: DashboardData, residentId: string, overrides: Partial<ResidentAlert> = {}): ResidentAlert {
  const resident = data.residents.find((r) => r.resident_id === residentId)
  if (!resident) throw new Error(`Not a linked demo resident: ${residentId}`)
  return {
    alert_id: `alert-test-${residentId}`,
    resident_id: residentId,
    device_id: resident.wearable.device_id,
    alert_type: 'fall',
    severity: 'critical',
    status: 'open',
    event_ts: new Date(Date.now() - 4 * 60_000).toISOString(),
    ...overrides,
  }
}

/** The demo caregiver data with these alerts instead of the demo ones. */
function withAlerts(alerts: (data: DashboardData) => ResidentAlert[]): DashboardData {
  const data = demoDataFor('family')
  return { ...data, alerts: alerts(data) }
}

// --- states ----------------------------------------------------------------

describe('loading, error, and empty states', () => {
  it('shows a loading status, the "My residents" header and the greeting while loading', async () => {
    const pending = deferred<DashboardData>()
    dashboardLoader.mockReturnValue(pending.promise)
    renderCaregiver()
    expect(screen.getByRole('status')).toHaveTextContent("Loading your residents' information")
    expect(screen.getByRole('heading', { level: 1, name: 'My residents' })).toBeInTheDocument()
    expect(screen.getByText(/^Good (morning|afternoon|evening), Test Caregiver A$/)).toBeInTheDocument()
    expect(dashboardLoader).toHaveBeenCalledWith('family')
    expect(screen.queryAllByRole('article')).toEqual([])
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull()

    pending.resolve(demoDataFor('family'))
    expect(await screen.findAllByRole('article')).toHaveLength(2)
    expect(screen.queryByText("Loading your residents' information")).not.toBeInTheDocument()
  })

  it('sets the page title to "My residents", without personal data', async () => {
    await renderLoaded()
    await waitFor(() => expect(document.title).toBe('SilverCare - My residents'))
    expect(document.title).not.toMatch(/Test|res-/)
  })

  it('never says "My family" or "family\'s"', async () => {
    const pending = deferred<DashboardData>()
    dashboardLoader.mockReturnValue(pending.promise)
    const { container } = renderCaregiver()
    expect(container).not.toHaveTextContent(/My family|family's/)
    pending.resolve(demoDataFor('family'))
    await screen.findAllByRole('article')
    expect(container).not.toHaveTextContent(/My family|family's/)
    expect(screen.queryByRole('heading', { name: 'My family' })).not.toBeInTheDocument()
  })

  it('shows an error with "Try again" when loading fails', async () => {
    dashboardLoader.mockRejectedValue(new Error('synthetic failure'))
    renderCaregiver()
    expect(await screen.findByText("We couldn't load the dashboard.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.queryAllByRole('article')).toEqual([])
    expect(screen.queryByText(/Test Resident/)).not.toBeInTheDocument()
  })

  it('"Try again" reloads and shows the residents', async () => {
    dashboardLoader.mockRejectedValueOnce(new Error('synthetic failure'))
    const { user } = renderCaregiver()
    await user.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findAllByRole('article')).toHaveLength(2)
    expect(dashboardLoader).toHaveBeenCalledTimes(2)
    expect(dashboardLoader).toHaveBeenLastCalledWith('family')
    expect(screen.queryByText("We couldn't load the dashboard.")).not.toBeInTheDocument()
  })

  it('the error notice is an alert, with "Try again" inside it', async () => {
    dashboardLoader.mockRejectedValue(new Error('synthetic failure'))
    renderCaregiver()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load the dashboard.")
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('shows the empty state when no residents are linked', async () => {
    dashboardLoader.mockResolvedValue({ residents: [], alerts: [] })
    renderCaregiver()
    expect(
      await screen.findByText('No residents are linked to your account yet. Your care provider can link you.'),
    ).toBeInTheDocument()
    expect(screen.queryAllByRole('article')).toEqual([])
  })
})

// --- residents -------------------------------------------------------------

describe('linked residents (demo data)', () => {
  it('shows only the 2 linked residents, Test Resident B and E, as named articles', async () => {
    await renderLoaded()
    const cards = screen.getAllByRole('article')
    expect(cards).toHaveLength(2)
    expect(within(cards[0]).getByRole('heading', { level: 2, name: 'Test Resident B' })).toBeInTheDocument()
    expect(within(cards[1]).getByRole('heading', { level: 2, name: 'Test Resident E' })).toBeInTheDocument()
    expect(screen.queryByText('Test Resident A')).not.toBeInTheDocument()
    expect(screen.queryByText('Test Resident C')).not.toBeInTheDocument()
  })

  it('each card shows the status as badge text and a plain sentence', async () => {
    const { data } = await renderLoaded()
    const labels = { ok: 'OK', attention: 'Attention', urgent: 'Urgent' } as const
    data.residents.forEach((r) => {
      const card = cardFor(r.name)
      const badge = card.querySelector(`[data-tone="${r.status}"]`) as HTMLElement
      expect(badge).toHaveTextContent(labels[r.status])
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(within(card).getByText(r.status_text)).toBeInTheDocument()
    })
  })

  it('each card shows the 4 latest vitals with labels and a text version of the trend', async () => {
    const { data } = await renderLoaded()
    data.residents.forEach((r) => {
      const card = cardFor(r.name)
      const latest = r.vitals[0]
      for (const label of ['Heart rate', 'SpO2', 'Skin temperature', 'Activity']) {
        expect(within(card).getByText(label)).toBeInTheDocument()
      }
      expect(card).toHaveTextContent(formatHr(latest.hr_bpm))
      expect(card).toHaveTextContent(formatSpo2(latest.spo2_pct))
      expect(card).toHaveTextContent(formatTemp(latest.skin_temp_c))
      expect(card).toHaveTextContent(formatActivity(latest.activity_pct))
      expect(within(card).getAllByText(/^Last 5 readings, oldest first: /)).toHaveLength(4)
    })
  })

  it('each card shows the wearable battery and status and the bedroom sensor status', async () => {
    const { data } = await renderLoaded()
    data.residents.forEach((r) => {
      const card = cardFor(r.name)
      expect(within(card).getByRole('heading', { level: 3, name: 'Devices' })).toBeInTheDocument()
      expect(within(card).getByText('Wearable')).toBeInTheDocument()
      expect(within(card).getByText('Bedroom sensor')).toBeInTheDocument()
      expect(card).toHaveTextContent(`${r.wearable.battery_pct}%`)
      expect(card).toHaveTextContent(r.wearable.worn ? 'Worn' : 'Not worn')
      expect(card).toHaveTextContent(r.bedroom.online ? 'Online' : 'Offline')
    })
  })

  it('shows no staff-only controls: no search, no status filter, no link to Devices', async () => {
    await renderLoaded()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Filter by status' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Devices' })).not.toBeInTheDocument()
    expect(document.querySelector('a[href="/devices"]')).toBeNull()
  })

  it('each vitals tile shows the time of the latest reading and a decorative sparkline', async () => {
    const { container } = await renderLoaded()
    screen.getAllByRole('article').forEach((card) => {
      // "At <time>": the label is the paragraph's own text, the time is in a child <time>.
      expect(within(card).getAllByText('At')).toHaveLength(4)
    })
    // Sparklines are decorative SVGs (aria-hidden), one per tile.
    expect(container.querySelectorAll('svg[aria-hidden="true"] polyline').length).toBeGreaterThanOrEqual(8)
  })

  it('shows "Offline" as text for an offline bedroom sensor', async () => {
    const data = demoDataFor('family')
    data.residents[0] = { ...data.residents[0], bedroom: { ...data.residents[0].bedroom, online: false } }
    await renderLoaded(data)
    expect(within(cardFor('Test Resident B')).getByText('Bedroom sensor').nextElementSibling).toHaveTextContent(
      'Offline',
    )
  })

  it('headings are in order: h1, then h2 per resident, then h3 sections', async () => {
    await renderLoaded()
    const levels = screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))
    expect(levels[0]).toBe(1)
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1)
    }
    expect(levels.filter((l) => l === 2)).toHaveLength(2)
  })

  it('cards do not link to the staff-only resident page', async () => {
    await renderLoaded()
    screen.getAllByRole('article').forEach((card) => {
      within(card)
        .queryAllByRole('link')
        .forEach((link) => expect(link.getAttribute('href') ?? '').not.toMatch(/^\/residents\//))
    })
  })
})

// --- recent alerts ---------------------------------------------------------

describe('recent alerts', () => {
  it('res-0002 lists its 2 demo warnings newest first; res-0005 has none', async () => {
    await renderLoaded()
    const items = timelineItems(cardFor('Test Resident B'))
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Wearable not worn')
    expect(items[1]).toHaveTextContent('Possible fall')
    items.forEach((item) => expect(within(item).getByText('Warning')).toBeInTheDocument())
    expect(within(cardFor('Test Resident E')).getByText('No recent alerts.')).toBeInTheDocument()
    expect(timelineItems(cardFor('Test Resident E'))).toEqual([])
  })

  it.each([
    ['hr_high', 'High heart rate'],
    ['hr_low', 'Low heart rate'],
    ['spo2_low', 'Low blood oxygen'],
    ['skin_temp_high', 'High skin temperature'],
    ['skin_temp_low', 'Low skin temperature'],
  ] as const)('labels a %s warning "%s", with its severity and no generic or "Info" text', async (type, label) => {
    await renderLoaded(
      withAlerts((d) => [
        alertFor(d, 'res-0002', {
          alert_type: type,
          severity: 'warning',
          event_ts: new Date(Date.now() - 60_000).toISOString(),
        }),
      ]),
    )
    const [item] = timelineItems(cardFor('Test Resident B'))
    expect(item).toHaveTextContent(label)
    expect(item).toHaveTextContent('Warning')
    expect(item).not.toHaveTextContent('Vitals need attention')
    expect(item).not.toHaveTextContent('Info')
  })

  it('the timeline is a polite live region in each card', async () => {
    await renderLoaded()
    screen.getAllByRole('article').forEach((card) => {
      expect(recentAlerts(card)).toHaveAttribute('aria-live', 'polite')
    })
  })

  it('shows at most 5 alerts, newest first', async () => {
    const data = withAlerts((d) =>
      Array.from({ length: 7 }, (_, n) =>
        alertFor(d, 'res-0005', {
          alert_id: `alert-test-${n}`,
          alert_type: 'battery_low',
          severity: 'warning',
          event_ts: new Date(Date.now() - (n + 1) * 60 * 60_000).toISOString(),
        }),
      ),
    )
    await renderLoaded(data)
    const items = timelineItems(cardFor('Test Resident E'))
    expect(items).toHaveLength(5)
    const times = items.map((i) => i.querySelector('time')?.getAttribute('datetime') as string)
    expect([...times].sort().reverse()).toEqual(times)
    expect(times[0]).toBe(data.alerts[0].event_ts)
  })

  it('the demo warnings show no escalation line: "Escalated to …" is never shown to a caregiver', async () => {
    const { data } = await renderLoaded()
    expect(data.alerts.some((a) => a.escalated_at && a.severity === 'warning')).toBe(true)
    const card = cardFor('Test Resident B')
    expect(card).not.toHaveTextContent(/Escalat|Sent to the care centre nurse|at once/)
  })

  it('an open critical alert with escalated_at shows "Sent to the care centre nurse at once"', async () => {
    const data = withAlerts((d) => [
      alertFor(d, 'res-0005', { escalated_at: new Date(Date.now() - 4 * 60_000 + 1_000).toISOString() }),
    ])
    await renderLoaded(data)
    const [item] = timelineItems(cardFor('Test Resident E'))
    expect(item).toHaveTextContent('Fall detected')
    expect(within(item).getByText('Critical')).toBeInTheDocument()
    const line = within(item).getByText('Sent to the care centre nurse at once')
    expect(line.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(line.closest('[aria-live="polite"]')).not.toBeNull()
  })

  it.each([
    ['an escalated warning', { severity: 'warning', alert_type: 'spo2_low', status: 'open' }],
    ['an acknowledged critical alert', { status: 'acknowledged', acknowledged_at: new Date().toISOString() }],
    ['a resolved critical alert', { status: 'resolved', resolved_by: 'Test Nurse A', resolved_at: new Date().toISOString() }],
    ['a cancelled critical alert', { status: 'cancelled', cancelled_at: new Date().toISOString(), cancelled_by_role: 'resident' }],
  ] as const)('%s shows no at-once or escalation line', async (_, fields) => {
    const data = withAlerts((d) => [
      alertFor(d, 'res-0005', { escalated_at: new Date(Date.now() - 60_000).toISOString(), ...fields }),
    ])
    await renderLoaded(data)
    const [item] = timelineItems(cardFor('Test Resident E'))
    expect(item).not.toHaveTextContent(/Sent to the care centre nurse|at once|Escalat/)
  })

  it('an open critical alert without escalated_at shows no at-once line yet', async () => {
    await renderLoaded(withAlerts((d) => [alertFor(d, 'res-0005')]))
    expect(timelineItems(cardFor('Test Resident E'))[0]).not.toHaveTextContent('at once')
  })

  it('an acknowledged alert shows "Acknowledged by the care centre nurse" and the nurse\'s note', async () => {
    const acknowledgedAt = new Date(Date.now() - 6 * 60_000).toISOString()
    const note = 'Synthetic note: called the caregiver.\nWearable to be checked at 3 pm.'
    const data = withAlerts((d) => [
      alertFor(d, 'res-0002', {
        alert_type: 'device_offline',
        severity: 'warning',
        status: 'acknowledged',
        event_ts: new Date(Date.now() - 20 * 60_000).toISOString(),
        acknowledged_at: acknowledgedAt,
        ack_note: note,
      }),
    ])
    await renderLoaded(data)
    const [item] = timelineItems(cardFor('Test Resident B'))
    const line = within(item).getByText(/^Acknowledged by the care centre nurse/)
    expect(line).toHaveTextContent(/^Acknowledged by the care centre nurse, 6 min ago/)
    expect(line.querySelector('time')).toHaveAttribute('datetime', acknowledgedAt)
    expect(within(item).getByText('Message from the care centre:')).toBeInTheDocument()
    const noteEl = within(item).getByText((_, el) => el?.tagName === 'P' && el.textContent === note)
    expect(noteEl).toHaveClass('whitespace-pre-line')
    expect(noteEl.children).toHaveLength(0)
    // Never a staff name.
    expect(item).not.toHaveTextContent(/Test Nurse/)
  })

  it('an acknowledged alert without a note shows the line but no note block', async () => {
    const data = withAlerts((d) => [
      alertFor(d, 'res-0002', { status: 'acknowledged', acknowledged_at: new Date(Date.now() - 60_000).toISOString() }),
    ])
    await renderLoaded(data)
    const [item] = timelineItems(cardFor('Test Resident B'))
    expect(item).toHaveTextContent('Acknowledged by the care centre nurse')
    expect(item).not.toHaveTextContent('Message from the care centre')
  })

  it('an alert never acknowledged shows no acknowledge line, even with a stray ack_note', async () => {
    await renderLoaded(withAlerts((d) => [alertFor(d, 'res-0002', { ack_note: 'Synthetic stray note' })]))
    const [item] = timelineItems(cardFor('Test Resident B'))
    expect(item).not.toHaveTextContent(/Acknowledged by|Message from the care centre|Synthetic stray note/)
  })

  it('has no Acknowledge, Resolve, or Cancel buttons, and no buttons in any card', async () => {
    const data = withAlerts((d) => [
      alertFor(d, 'res-0005', { escalated_at: new Date().toISOString() }),
      alertFor(d, 'res-0002', {
        alert_id: 'alert-test-ack',
        status: 'acknowledged',
        acknowledged_at: new Date().toISOString(),
        ack_note: 'Synthetic note',
      }),
    ])
    await renderLoaded(data)
    expect(screen.queryAllByRole('button', { name: /Acknowledge|Resolve|Cancel/ })).toEqual([])
    screen.getAllByRole('article').forEach((card) => expect(within(card).queryAllByRole('button')).toEqual([]))
  })
})

// --- status from the data --------------------------------------------------

describe('status comes from the data (criteria 19, 22)', () => {
  it('shows the provided status, not one computed from vitals', async () => {
    const [b, e] = demoDataFor('family').residents
    const extremeButOk = {
      ...b,
      status: 'ok' as const,
      status_text: 'All readings look normal',
      vitals: b.vitals.map((v) => ({ ...v, hr_bpm: 220, spo2_pct: 55 })),
    }
    const normalButUrgent = { ...e, status: 'urgent' as const, status_text: 'Urgent: check now' }
    await renderLoaded({ residents: [extremeButOk, normalButUrgent], alerts: [] })
    expect(within(cardFor('Test Resident B')).getByText('OK')).toBeInTheDocument()
    expect(within(cardFor('Test Resident B')).getByText('220 bpm')).toBeInTheDocument()
    expect(within(cardFor('Test Resident E')).getByText('Urgent')).toBeInTheDocument()
    expect(within(cardFor('Test Resident E')).getByText('Urgent: check now')).toBeInTheDocument()
  })

  it('every badge, resident status and alert severity alike, has an icon and a text label', async () => {
    const { container } = await renderLoaded()
    const badges = container.querySelectorAll('[data-tone]')
    // 2 resident status badges plus res-0002's 2 alert severity badges.
    expect(badges.length).toBeGreaterThan(2)
    badges.forEach((badge) => {
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(badge.textContent?.trim()).not.toBe('')
    })
  })

  it('shows "No reading" for a missing SpO2 value', async () => {
    const [b, e] = demoDataFor('family').residents
    const missing = { ...b, vitals: b.vitals.map((v, i) => (i === 0 ? { ...v, spo2_pct: null } : v)) }
    await renderLoaded({ residents: [missing, e], alerts: [] })
    expect(within(cardFor('Test Resident B')).getByText('No reading')).toBeInTheDocument()
  })
})

// --- device-problem acknowledge (user's decision 2026-11-05) ----------------

describe('acknowledging a device problem', () => {
  const B_NOT_WORN = 'Acknowledge: Wearable not worn, Test Resident B'

  it('an open device problem has an Acknowledge button; health alerts have none', async () => {
    await renderLoaded()
    // res-0002's demo alerts: an open wearable-not-worn and an open possible fall.
    expect(screen.getAllByRole('button', { name: /^Acknowledge/ }).map((b) => b.textContent)).toEqual([B_NOT_WORN])
    const [notWorn, possibleFall] = timelineItems(cardFor('Test Resident B'))
    expect(within(notWorn).getByRole('button', { name: B_NOT_WORN })).toBeInTheDocument()
    expect(within(possibleFall).queryByRole('button')).not.toBeInTheDocument()
  })

  it.each([
    ['battery_low', 'Low battery'],
    ['device_offline', 'Device offline'],
    ['not_worn', 'Wearable not worn'],
  ] as const)('offers Acknowledge on an open %s', async (alert_type, label) => {
    await renderLoaded(withAlerts((d) => [alertFor(d, 'res-0005', { alert_type, severity: 'warning' })]))
    expect(screen.getByRole('button', { name: `Acknowledge: ${label}, Test Resident E` })).toBeInTheDocument()
  })

  it.each([
    ['an open fall', { alert_type: 'fall', severity: 'critical' }],
    ['an open vitals warning', { alert_type: 'spo2_low', severity: 'warning' }],
    ['an acknowledged device problem', { alert_type: 'battery_low', severity: 'warning', status: 'acknowledged', acknowledged_at: new Date().toISOString(), acknowledged_by_role: 'caregiver', acknowledged_by: 'Test Caregiver B' }],
    ['a resolved device problem', { alert_type: 'battery_low', severity: 'warning', status: 'resolved', resolved_by: 'Test Nurse A', resolved_at: new Date().toISOString() }],
  ] as const)('offers no Acknowledge on %s', async (_, fields) => {
    await renderLoaded(withAlerts((d) => [alertFor(d, 'res-0005', fields)]))
    expect(screen.queryByRole('button', { name: /^Acknowledge/ })).not.toBeInTheDocument()
  })

  it('acknowledges with the caregiver\'s own name, removes the button, and moves focus to "Recent alerts"', async () => {
    const { user } = await renderLoaded()
    const card = cardFor('Test Resident B')
    await user.click(screen.getByRole('button', { name: B_NOT_WORN }))

    const [notWorn] = timelineItems(card)
    expect(notWorn).toHaveTextContent(/Acknowledged by Test Caregiver A \(caregiver\), just now/)
    expect(notWorn).not.toHaveTextContent(/care centre nurse|Message from the care centre/)
    expect(screen.queryByRole('button', { name: B_NOT_WORN })).not.toBeInTheDocument()
    expect(within(card).getByRole('heading', { level: 3, name: 'Recent alerts' })).toHaveFocus()
    // The new line is inside the timeline's live region, so screen readers hear it.
    expect(within(notWorn).getByText(/^Acknowledged by Test Caregiver A/).closest('[aria-live="polite"]')).not.toBeNull()
  })

  it('shows another caregiver\'s acknowledge as "Acknowledged by <name> (caregiver)"', async () => {
    await renderLoaded(
      withAlerts((d) => [
        alertFor(d, 'res-0005', {
          alert_type: 'device_offline',
          severity: 'warning',
          status: 'acknowledged',
          acknowledged_at: new Date(Date.now() - 2 * 60_000).toISOString(),
          acknowledged_by_role: 'caregiver',
          acknowledged_by: 'Test Caregiver B',
        }),
      ]),
    )
    expect(timelineItems(cardFor('Test Resident E'))[0]).toHaveTextContent(
      /Acknowledged by Test Caregiver B \(caregiver\), 2 min ago/,
    )
  })
})

// --- accessibility ---------------------------------------------------------

describe('accessibility', { timeout: 30_000 }, () => {
  it('has no axe violations after acknowledging a device problem', async () => {
    const { user, container } = await renderLoaded()
    await user.click(screen.getByRole('button', { name: 'Acknowledge: Wearable not worn, Test Resident B' }))
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations in the empty state', async () => {
    dashboardLoader.mockResolvedValue({ residents: [], alerts: [] })
    const { container } = renderCaregiver()
    await screen.findByText(/No residents are linked/)
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations (demo data)', async () => {
    const { container } = await renderLoaded()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations with an at-once line, an acknowledge line, and a note', async () => {
    const data = withAlerts((d) => [
      alertFor(d, 'res-0005', { escalated_at: new Date().toISOString() }),
      alertFor(d, 'res-0002', {
        alert_id: 'alert-test-ack',
        status: 'acknowledged',
        acknowledged_at: new Date().toISOString(),
        ack_note: 'Synthetic note: on the way.',
      }),
    ])
    const { container } = await renderLoaded(data)
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations while loading', async () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    const { container } = renderCaregiver()
    expect(await axeViolations(container)).toEqual([])
  })
})
