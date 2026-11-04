import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
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
  it('res-0002 lists its 3 demo warnings newest first; res-0005 has none', async () => {
    await renderLoaded()
    const items = timelineItems(cardFor('Test Resident B'))
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('Inactivity')
    // The newest is mid-countdown for the caregiver; the older one has escalated.
    expect(within(items[0]).getByRole('timer')).toBeInTheDocument()
    expect(items[1]).toHaveTextContent('Inactivity')
    expect(items[1]).toHaveTextContent('Escalated to the care centre nurse')
    expect(items[2]).toHaveTextContent('Possible fall')
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

  it('has one page announcer and no live region around the timelines (alert-actions-ui, criterion 16)', async () => {
    const { container } = await renderLoaded()
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(container.querySelectorAll('[aria-live]')).toHaveLength(1)
    screen.getAllByRole('article').forEach((card) => {
      expect(recentAlerts(card)).not.toHaveAttribute('aria-live')
      expect(card.querySelector('[aria-live]')).toBeNull()
    })
  })

  it('shows at most 5 alerts, newest first', async () => {
    const data = withAlerts((d) =>
      Array.from({ length: 7 }, (_, n) =>
        alertFor(d, 'res-0005', {
          alert_id: `alert-test-${n}`,
          alert_type: 'low_battery',
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

  it('an escalated demo warning shows "Escalated to the care centre nurse" (the caregiver is not the recipient)', async () => {
    const { data } = await renderLoaded()
    const escalated = data.alerts.filter((a) => a.resident_id === 'res-0002' && a.escalated_at && a.status === 'open')
    expect(escalated.length).toBeGreaterThan(0)
    const card = cardFor('Test Resident B')
    expect(within(card).getAllByText('Escalated to the care centre nurse')).toHaveLength(escalated.length)
    expect(card).not.toHaveTextContent('at once')
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
  })

  it('an escalated open warning shows "Escalated to the care centre nurse", not "at once"', async () => {
    const data = withAlerts((d) => [
      alertFor(d, 'res-0005', {
        severity: 'warning',
        alert_type: 'inactivity',
        escalated_at: new Date(Date.now() - 60_000).toISOString(),
      }),
    ])
    await renderLoaded(data)
    const [item] = timelineItems(cardFor('Test Resident E'))
    expect(within(item).getByText('Escalated to the care centre nurse')).toBeInTheDocument()
    expect(item).not.toHaveTextContent('at once')
  })

  it.each([
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

  it('has no Acknowledge or Resolve buttons; the only button per alert is "Cancel alert"', async () => {
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
    expect(screen.queryAllByRole('button', { name: /Acknowledge|Resolve/ })).toEqual([])
    screen.getAllByRole('article').forEach((card) =>
      within(card)
        .getAllByRole('button')
        .forEach((button) => expect(button).toHaveAccessibleName(/^Cancel alert: /)),
    )
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

// --- countdown to escalation, for the caregiver ------------------------------

describe('countdown to escalation (fake clock)', () => {
  const NOW = new Date('2026-10-18T02:30:00Z')
  const INACTIVITY = 'Inactivity, Test Resident E'

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  async function renderWarning(overrides: Partial<ResidentAlert> = {}) {
    const data = demoDataFor('family', NOW)
    const resident = data.residents.find((r) => r.resident_id === 'res-0005')
    if (!resident) throw new Error('res-0005 is not linked')
    const warning: ResidentAlert = {
      alert_id: 'alert-test-countdown',
      resident_id: 'res-0005',
      device_id: resident.bedroom.device_id,
      alert_type: 'inactivity',
      severity: 'warning',
      status: 'open',
      event_ts: new Date(NOW.getTime() - 25_000).toISOString(),
      escalates_at: new Date(NOW.getTime() + 275_000).toISOString(),
      ...overrides,
    }
    dashboardLoader.mockResolvedValue({ ...data, alerts: [warning] })
    renderWithAuth(<CaregiverView />, { user: TEST_USERS.caregiver })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getAllByRole('article')).toHaveLength(2)
  }

  const tick = (ms: number) =>
    act(() => {
      vi.advanceTimersByTime(ms)
    })
  const timer = () => within(cardFor('Test Resident E')).getByRole('timer')
  const announcer = () => screen.getByRole('status')

  it('tells the caregiver how long they have to cancel, as m:ss, ticking every second', async () => {
    await renderWarning()
    expect(timer()).toHaveTextContent(/^The care centre nurse will be alerted in 4:35 unless you cancel this alert$/)
    expect(timer().parentElement?.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    tick(1000)
    expect(timer()).toHaveTextContent('in 4:34 unless')
    tick(214_000)
    expect(timer()).toHaveTextContent('in 1:00 unless')
    tick(59_000)
    expect(timer()).toHaveTextContent('in 0:01 unless')
    // The countdown is not a live region; the page's announcer stays quiet until escalation.
    expect(timer().closest('[aria-live]')).toBeNull()
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('at zero it shows "Escalated to the care centre nurse" and announces it once', async () => {
    await renderWarning()
    tick(275_000)
    expect(within(cardFor('Test Resident E')).queryByRole('timer')).not.toBeInTheDocument()
    expect(cardFor('Test Resident E')).toHaveTextContent('Escalated to the care centre nurse')
    expect(announcer()).toHaveTextContent(`Escalated to the care centre nurse: ${INACTIVITY}.`)
    const announced = announcer().textContent
    tick(5_000)
    expect(announcer().textContent).toBe(announced)
  })

  it('cancelling before the deadline removes the countdown and nothing escalates', async () => {
    await renderWarning()
    tick(60_000)
    fireEvent.click(screen.getByRole('button', { name: `Cancel alert: ${INACTIVITY}` }))
    fireEvent.click(screen.getByRole('button', { name: `Yes, cancel alert: ${INACTIVITY}` }))
    expect(within(cardFor('Test Resident E')).queryByRole('timer')).not.toBeInTheDocument()
    expect(announcer()).toHaveTextContent(`Alert cancelled: ${INACTIVITY}.`)
    const announced = announcer().textContent
    tick(5 * 60_000)
    expect(announcer().textContent).toBe(announced)
    expect(cardFor('Test Resident E')).not.toHaveTextContent(/Escalat/)
  })

  it('the demo data shows B\'s newest inactivity warning mid-countdown, 3:00 left on load', async () => {
    dashboardLoader.mockResolvedValue(demoDataFor('family', NOW))
    renderWithAuth(<CaregiverView />, { user: TEST_USERS.caregiver })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    const timers = within(cardFor('Test Resident B')).getAllByRole('timer')
    expect(timers).toHaveLength(1)
    expect(timers[0]).toHaveTextContent(/^The care centre nurse will be alerted in 3:00 unless you cancel this alert$/)
    expect(within(cardFor('Test Resident E')).queryByRole('timer')).not.toBeInTheDocument()
    tick(180_000)
    expect(screen.getByRole('status')).toHaveTextContent('Escalated to the care centre nurse: Inactivity, Test Resident B.')
  })

  it('an acknowledged alert shows no countdown', async () => {
    await renderWarning({ status: 'acknowledged', acknowledged_at: NOW.toISOString() })
    expect(within(cardFor('Test Resident E')).queryByRole('timer')).not.toBeInTheDocument()
    tick(5 * 60_000)
    expect(cardFor('Test Resident E')).not.toHaveTextContent(/Escalat/)
    expect(announcer()).toBeEmptyDOMElement()
  })
})

// --- caregiver cancel (alert-actions-ui, criteria 10-16) ---------------------

describe('caregiver cancel', () => {
  const INACTIVITY = 'Inactivity, Test Resident B'
  // B has two demo inactivity warnings (one mid-countdown), so the demo-data tests use
  // B's only possible fall.
  const POSSIBLE_FALL = 'Possible fall, Test Resident B'
  const cancelButton = (context: string) => screen.getByRole('button', { name: `Cancel alert: ${context}` })
  const itemFor = (context: string) => cancelButton(context).closest('li') as HTMLElement
  const announcer = () => screen.getByRole('status')

  it('each open or acknowledged alert has "Cancel alert" naming the alert; cancelled and resolved have none', async () => {
    const data = withAlerts((d) => [
      alertFor(d, 'res-0002', { alert_id: 'a-open', alert_type: 'inactivity', severity: 'warning' }),
      alertFor(d, 'res-0002', {
        alert_id: 'a-ack',
        alert_type: 'low_battery',
        severity: 'warning',
        status: 'acknowledged',
        acknowledged_at: new Date(Date.now() - 60_000).toISOString(),
      }),
      alertFor(d, 'res-0005', {
        alert_id: 'a-cancelled',
        status: 'cancelled',
        cancelled_at: new Date(Date.now() - 60_000).toISOString(),
        cancelled_by_role: 'resident',
      }),
      alertFor(d, 'res-0005', {
        alert_id: 'a-resolved',
        alert_type: 'hr_high',
        status: 'resolved',
        resolved_by: 'Care centre nurse',
        resolved_at: new Date(Date.now() - 60_000).toISOString(),
      }),
    ])
    await renderLoaded(data)
    expect(cancelButton(INACTIVITY)).toBeInTheDocument()
    expect(cancelButton('Low battery, Test Resident B')).toBeInTheDocument()
    expect(within(cardFor('Test Resident E')).queryAllByRole('button')).toEqual([])
    // Lifecycle as text, and the lines for each state.
    const items = timelineItems(cardFor('Test Resident E'))
    const cancelledItem = items.find((i) => i.textContent?.includes('Fall detected')) as HTMLElement
    const resolvedItem = items.find((i) => i.textContent?.includes('High heart rate')) as HTMLElement
    expect(within(cancelledItem).getByText('Cancelled')).toBeInTheDocument()
    expect(cancelledItem).toHaveTextContent(`Cancelled by the resident ("I'm OK"), 1 min ago`)
    expect(within(resolvedItem).getByText('Resolved')).toBeInTheDocument()
    expect(resolvedItem).toHaveTextContent('Resolved by Care centre nurse, 1 min ago')
    expect(within(itemFor('Low battery, Test Resident B')).getByText('Acknowledged')).toBeInTheDocument()
    expect(within(itemFor(INACTIVITY)).getByText('Open')).toBeInTheDocument()
  })

  it('"Cancel alert" asks to confirm first, with focus on "Keep alert", and changes nothing yet', async () => {
    const { user } = await renderLoaded()
    const item = itemFor(POSSIBLE_FALL)
    await user.click(cancelButton(POSSIBLE_FALL))
    const prompt = within(item).getByRole('group', { name: /^Cancel this alert\?/ })
    expect(prompt).toHaveTextContent(
      "Cancel this alert? It will no longer be escalated to the care centre nurse. This resident's caregivers, and the care centre nurse if they were alerted, will be emailed that you cancelled it.",
    )
    expect(within(prompt).getByRole('button', { name: `Keep alert: ${POSSIBLE_FALL}` })).toHaveFocus()
    expect(within(prompt).getByRole('button', { name: `Yes, cancel alert: ${POSSIBLE_FALL}` })).toBeInTheDocument()
    expect(within(item).getByText('Open')).toBeInTheDocument()
    expect(item).not.toHaveTextContent('Cancelled by')
  })

  it('"Keep alert" closes the confirmation, changes nothing, and returns focus to "Cancel alert"', async () => {
    const { user } = await renderLoaded()
    await user.click(cancelButton(POSSIBLE_FALL))
    await user.click(screen.getByRole('button', { name: `Keep alert: ${POSSIBLE_FALL}` }))
    expect(screen.queryByRole('group', { name: /^Cancel this alert\?/ })).not.toBeInTheDocument()
    expect(cancelButton(POSSIBLE_FALL)).toHaveFocus()
    expect(within(itemFor(POSSIBLE_FALL)).getByText('Open')).toBeInTheDocument()
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('"Yes, cancel alert" cancels it as the caregiver, moves focus to "Recent alerts", and announces it', async () => {
    const { user } = await renderLoaded()
    const card = cardFor('Test Resident B')
    const item = itemFor(POSSIBLE_FALL)
    await user.click(cancelButton(POSSIBLE_FALL))
    await user.click(screen.getByRole('button', { name: `Yes, cancel alert: ${POSSIBLE_FALL}` }))
    expect(item).toHaveTextContent('Possible fall')
    expect(within(item).getByText('Cancelled')).toBeInTheDocument()
    expect(item).toHaveTextContent('Cancelled by Test Caregiver A (caregiver), just now')
    expect(within(item).queryByRole('button')).not.toBeInTheDocument()
    expect(within(card).getByRole('heading', { level: 3, name: 'Recent alerts' })).toHaveFocus()
    expect(announcer()).toHaveTextContent(`Alert cancelled: ${POSSIBLE_FALL}.`)
    // The other alerts (B's two inactivity warnings) are untouched.
    expect(screen.getAllByRole('button', { name: `Cancel alert: ${INACTIVITY}` })).toHaveLength(2)
  })

  it('cancelling an acknowledged alert keeps the acknowledge line and note', async () => {
    const note = 'Synthetic note: the care centre is calling.'
    const data = withAlerts((d) => [
      alertFor(d, 'res-0002', {
        status: 'acknowledged',
        acknowledged_at: new Date(Date.now() - 2 * 60_000).toISOString(),
        ack_note: note,
      }),
    ])
    const { user } = await renderLoaded(data)
    await user.click(cancelButton('Fall detected, Test Resident B'))
    await user.click(screen.getByRole('button', { name: 'Yes, cancel alert: Fall detected, Test Resident B' }))
    const [item] = timelineItems(cardFor('Test Resident B'))
    expect(item).toHaveTextContent('Acknowledged by the care centre nurse, 2 min ago')
    expect(within(item).getByText(note)).toBeInTheDocument()
    expect(item).toHaveTextContent('Cancelled by Test Caregiver A (caregiver), just now')
  })

  it('stores nothing in the browser and logs nothing when cancelling', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const log = vi.spyOn(console, 'log')
    const { user } = await renderLoaded()
    await user.click(cancelButton(POSSIBLE_FALL))
    await user.click(screen.getByRole('button', { name: `Yes, cancel alert: ${POSSIBLE_FALL}` }))
    expect(setItem).not.toHaveBeenCalled()
    expect(log).not.toHaveBeenCalled()
    expect(window.location.href).not.toMatch(/res-|Test|alert/)
    setItem.mockRestore()
    log.mockRestore()
  })
})

// --- accessibility ---------------------------------------------------------

describe('accessibility', { timeout: 30_000 }, () => {
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

  it('has no axe violations with the cancel confirmation open, and after cancelling', async () => {
    const { user, container } = await renderLoaded()
    await user.click(screen.getByRole('button', { name: 'Cancel alert: Possible fall, Test Resident B' }))
    expect(await axeViolations(container)).toEqual([])
    await user.click(screen.getByRole('button', { name: 'Yes, cancel alert: Possible fall, Test Resident B' }))
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations while loading', async () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    const { container } = renderCaregiver()
    expect(await axeViolations(container)).toEqual([])
  })
})
