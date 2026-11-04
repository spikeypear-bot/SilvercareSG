import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { dashboardLoader, demoDataFor, resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import { DoctorDashboard } from './DoctorDashboard'
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

function renderDoctor() {
  const user = userEvent.setup()
  const view = renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
  return { user, ...view }
}

async function renderLoaded(data: DashboardData = demoDataFor('staff')) {
  dashboardLoader.mockResolvedValue(data)
  const ctx = renderDoctor()
  await screen.findByRole('heading', { level: 2, name: 'Residents' })
  return { ...ctx, data }
}

const residentCards = () => screen.queryAllByRole('article')
const summary = () => screen.getByRole('region', { name: /^Open alerts/ })
const summaryRows = () => {
  const list = summary().querySelector('ul')
  return list ? within(list as HTMLElement).getAllByRole('listitem') : []
}
const statsSection = () => screen.getByRole('region', { name: 'Summary' })
const tileValue = (label: string) =>
  within(statsSection()).getByText(label, { selector: 'span' }).nextElementSibling?.textContent
const cardFor = (name: string) =>
  within(screen.getByRole('heading', { level: 3, name }).closest('article') as HTMLElement)

function alertFor(
  n: number,
  overrides: Partial<ResidentAlert> & Pick<ResidentAlert, 'alert_type' | 'severity'>,
): ResidentAlert {
  const id = `res-${String(n).padStart(4, '0')}`
  return {
    alert_id: `alert-test-${String(n).padStart(3, '0')}`,
    resident_id: id,
    device_id: `wearable-test-${n}`,
    status: 'open',
    event_ts: new Date(Date.now() - n * 60_000).toISOString(),
    ...overrides,
  }
}

describe('loading, error, and layout', () => {
  it('shows skeletons with a loading status, plus header, banner, and greeting', () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    renderDoctor()
    expect(screen.getByRole('status')).toHaveTextContent('Loading dashboard')
    expect(screen.getByRole('heading', { level: 1, name: 'Doctor dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('note', { name: 'Demo data' })).toHaveTextContent(BANNER)
    expect(screen.getByText(/^Good (morning|afternoon|evening), Test Doctor A$/)).toBeInTheDocument()
    expect(residentCards()).toHaveLength(0)
    expect(dashboardLoader).toHaveBeenCalledWith('staff')
  })

  it('shows an error notice with Try again, which reloads', async () => {
    dashboardLoader.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderDoctor()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load the dashboard.")
    expect(residentCards()).toHaveLength(0)
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Residents' })).toBeInTheDocument()
    expect(dashboardLoader).toHaveBeenCalledTimes(2)
  })

  it('keeps the demo banner visible in the error state', async () => {
    dashboardLoader.mockRejectedValue(new Error('boom'))
    renderDoctor()
    await screen.findByRole('alert')
    expect(screen.getByRole('note', { name: 'Demo data' })).toHaveTextContent(BANNER)
  })

  it('page title has no personal data', async () => {
    renderDoctor()
    await waitFor(() => expect(document.title).toBe('SilverCare - Doctor dashboard'))
  })
})

describe('review only: no alert actions, static tiles', () => {
  it('has no Acknowledge or Resolve buttons anywhere', async () => {
    await renderLoaded()
    expect(screen.queryAllByRole('button', { name: /Acknowledge|Resolve/ })).toEqual([])
  })

  it('tiles are static cards, not buttons, and show the numbers from the data', async () => {
    await renderLoaded()
    expect(screen.queryByRole('group', { name: 'Choose what the panel below shows' })).not.toBeInTheDocument()
    expect(within(statsSection()).queryAllByRole('button')).toEqual([])
    expect(screen.queryAllByRole('button', { pressed: true }).filter((b) => statsSection().contains(b))).toEqual([])

    expect(tileValue('Residents monitored')).toBe('20')
    expect(tileValue('Open alerts')).toBe('13')
    expect(within(statsSection()).getByText('3 critical')).toBeInTheDocument()
    // 6 offline devices (L and O wearables; F, I, O, Q bedroom sensors).
    expect(tileValue('Devices offline')).toBe('6')
    // Wearables only (F 15%, M 12%).
    expect(tileValue('Wearables low on battery')).toBe('2')
    expect(within(statsSection()).getByText('Below 20%')).toBeInTheDocument()
  })

  it('shows the residents and the alert summary together (no panel switching)', async () => {
    await renderLoaded()
    expect(residentCards()).toHaveLength(20)
    expect(summary()).toBeInTheDocument()
  })
})

describe('open alerts summary', () => {
  it('shows the count, critical and acknowledged numbers, and who responds', async () => {
    await renderLoaded()
    expect(screen.getByRole('heading', { level: 2, name: 'Open alerts (13)' })).toBeInTheDocument()
    expect(summary()).toHaveTextContent(
      '3 critical · 3 acknowledged. Nurses respond to alerts; the 3 most urgent are shown here.',
    )
  })

  it('shows the 3 most urgent alerts, critical first, as tiles without actions', async () => {
    await renderLoaded()
    const rows = summaryRows()
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Critical')
    expect(rows[0]).toHaveTextContent('Fall detected')
    expect(rows[0]).toHaveTextContent('Test Resident C')
    expect(rows[0]).toHaveTextContent('res-0003')
    expect(rows[1]).toHaveTextContent('High heart rate')
    expect(rows[1]).toHaveTextContent('Test Resident G')
    expect(rows[1]).toHaveTextContent('Acknowledged by the care centre nurse')
    expect(within(rows[1]).getByText('Message from the care centre:')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Synthetic note: ambulance on the way, 8 minutes.')).toBeInTheDocument()
    expect(rows[2]).toHaveTextContent('Fall detected')
    expect(rows[2]).toHaveTextContent('Test Resident Q')
    // The open critical alerts were sent to the nurse at once.
    expect(rows[0]).toHaveTextContent('Sent to the care centre nurse at once')
    expect(rows[2]).toHaveTextContent('Sent to the care centre nurse at once')
    rows.forEach((row) => {
      expect(row).toHaveTextContent('Critical')
      expect(within(row).queryByRole('button')).not.toBeInTheDocument()
    })
  })

  it('each summary row shows the home ID and a relative time', async () => {
    const { data } = await renderLoaded()
    const residentC = data.residents.find((r) => r.resident_id === 'res-0003')
    const row = summaryRows()[0]
    expect(row).toHaveTextContent(`Home${residentC?.home_id}`)
    expect(within(row).getByText('4 min ago').closest('time')).toHaveAttribute('datetime')
  })

  it('without critical alerts, shows the newest 3', async () => {
    const data = demoDataFor('staff')
    const alerts = [5, 1, 4, 2, 3].map((n) => alertFor(n, { alert_type: 'inactivity', severity: 'warning' }))
    await renderLoaded({ ...data, alerts })
    const rows = summaryRows()
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Test Resident A')
    expect(rows[1]).toHaveTextContent('Test Resident B')
    expect(rows[2]).toHaveTextContent('Test Resident C')
    expect(summary()).toHaveTextContent('0 critical · 0 acknowledged.')
    expect(screen.getByRole('heading', { level: 2, name: 'Open alerts (5)' })).toBeInTheDocument()
  })

  it('a critical alert older than the warnings is still pinned first', async () => {
    const data = demoDataFor('staff')
    const alerts = [
      alertFor(1, { alert_type: 'inactivity', severity: 'warning' }),
      alertFor(2, { alert_type: 'low_battery', severity: 'warning' }),
      alertFor(3, { alert_type: 'possible_fall', severity: 'warning' }),
      alertFor(19, { alert_type: 'fall', severity: 'critical' }),
    ]
    await renderLoaded({ ...data, alerts })
    const rows = summaryRows()
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Fall detected')
    expect(rows[0]).toHaveTextContent('Test Resident S')
    expect(rows[1]).toHaveTextContent('Test Resident A')
    expect(rows[2]).toHaveTextContent('Test Resident B')
  })

  it('with fewer than 3 alerts, shows them all and says how many', async () => {
    const data = demoDataFor('staff')
    const alerts = [
      alertFor(1, { alert_type: 'fall', severity: 'critical' }),
      alertFor(2, { alert_type: 'inactivity', severity: 'warning' }),
    ]
    await renderLoaded({ ...data, alerts })
    expect(summaryRows()).toHaveLength(2)
    expect(summary()).toHaveTextContent('1 critical · 0 acknowledged. Nurses respond to alerts; the 2 most urgent are shown here.')
  })

  it('leaves out resolved alerts', async () => {
    const data = demoDataFor('staff')
    const alerts = [
      alertFor(1, { alert_type: 'fall', severity: 'critical', status: 'resolved', resolved_by: 'Test Nurse C', resolved_at: new Date().toISOString() }),
      alertFor(2, { alert_type: 'inactivity', severity: 'warning' }),
    ]
    await renderLoaded({ ...data, alerts })
    expect(summaryRows()).toHaveLength(1)
    expect(summaryRows()[0]).toHaveTextContent('Test Resident B')
    expect(summary()).toHaveTextContent(
      '0 critical · 0 acknowledged. Nurses respond to alerts; the most urgent is shown here.',
    )
    expect(summary()).not.toHaveTextContent('1 most urgent')
    expect(screen.getByRole('heading', { level: 2, name: 'Open alerts (1)' })).toBeInTheDocument()
    expect(tileValue('Open alerts')).toBe('1')
  })

  it('leaves out cancelled alerts from the summary, its counts, and the tiles', async () => {
    const data = demoDataFor('staff')
    const cancelled = { status: 'cancelled' as const, cancelled_at: new Date().toISOString(), cancelled_by_role: 'resident' as const }
    const alerts = [
      alertFor(1, { alert_type: 'fall', severity: 'critical', ...cancelled }),
      alertFor(2, { alert_type: 'inactivity', severity: 'warning' }),
      alertFor(3, { alert_type: 'hr_high', severity: 'warning', ...cancelled }),
    ]
    await renderLoaded({ ...data, alerts })
    expect(summaryRows()).toHaveLength(1)
    expect(summaryRows()[0]).toHaveTextContent('Test Resident B')
    expect(summary()).not.toHaveTextContent('Cancelled')
    expect(summary()).not.toHaveTextContent('Test Resident A')
    expect(summary()).not.toHaveTextContent('Test Resident C')
    expect(summary()).toHaveTextContent('0 critical · 0 acknowledged. Nurses respond to alerts; the most urgent is shown here.')
    expect(screen.getByRole('heading', { level: 2, name: 'Open alerts (1)' })).toBeInTheDocument()
    expect(tileValue('Open alerts')).toBe('1')
    expect(within(statsSection()).getByText('0 critical')).toBeInTheDocument()
  })

  it('shows "No open alerts." when every alert is cancelled', async () => {
    const data = demoDataFor('staff')
    const alerts = [
      alertFor(1, { alert_type: 'fall', severity: 'critical', status: 'cancelled', cancelled_at: new Date().toISOString(), cancelled_by_role: 'resident' }),
    ]
    await renderLoaded({ ...data, alerts })
    expect(within(summary()).getByText('No open alerts.')).toBeInTheDocument()
    expect(tileValue('Open alerts')).toBe('0')
  })

  it('shows "No open alerts." when there are none', async () => {
    await renderLoaded({ ...demoDataFor('staff'), alerts: [] })
    expect(within(summary()).getByText('No open alerts.')).toBeInTheDocument()
    expect(summary()).toHaveTextContent('0 critical · 0 acknowledged. Nurses respond to alerts.')
    expect(summary()).not.toHaveTextContent('most urgent')
    expect(tileValue('Open alerts')).toBe('0')
  })

  it('shows the escalation countdown for an open alert in its window', async () => {
    const data = demoDataFor('staff')
    const alerts = [
      alertFor(8, {
        alert_type: 'possible_fall',
        severity: 'warning',
        escalates_at: new Date(Date.now() + 275_000).toISOString(),
      }),
    ]
    await renderLoaded({ ...data, alerts })
    expect(within(summaryRows()[0]).getByRole('timer')).toHaveTextContent(
      /^Escalates to the care centre nurse in 4:[0-3]\d$/,
    )
  })

  it('the summary is not a live region; the announcer is, and is quiet on load', async () => {
    await renderLoaded()
    expect(summary().closest('[aria-live]')).toBeNull()
    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-live', 'polite')
    expect(status).toBeEmptyDOMElement()
  })
})

describe('escalation announcements (fake clock)', () => {
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

  async function renderAtNow() {
    dashboardLoader.mockResolvedValue(demoDataFor('staff', NOW))
    renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByRole('heading', { level: 2, name: 'Residents' })).toBeInTheDocument()
  }

  const tick = (ms: number) =>
    act(() => {
      vi.advanceTimersByTime(ms)
    })

  it('announces an escalation of an alert outside the top 3 shown', async () => {
    await renderAtNow()
    // The 3 criticals fill the summary; Test Resident H's possible fall is not shown.
    expect(summaryRows()).toHaveLength(3)
    summaryRows().forEach((row) => expect(row).not.toHaveTextContent('Test Resident H'))
    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    tick(149_000)
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    tick(1000)
    expect(screen.getByRole('status')).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)

    // B's inactivity warning (the caregiver view's demo countdown) escalates 30 s later.
    tick(29_000)
    expect(screen.getByRole('status')).toHaveTextContent(`Escalated to the care centre nurse: ${H}.`)
    tick(1000)
    expect(screen.getByRole('status')).toHaveTextContent('Escalated to the care centre nurse: Inactivity, Test Resident B.')

    const announced = screen.getByRole('status').textContent
    tick(5 * 60_000)
    expect(screen.getByRole('status').textContent).toBe(announced)
  })

  it('does not announce alerts that had already escalated when the page opened', async () => {
    const data = demoDataFor('staff', NOW)
    // Drop the two mid-countdown alerts; every other alert has already escalated.
    dashboardLoader.mockResolvedValue({ ...data, alerts: data.alerts.filter((a) => !a.escalates_at) })
    renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    tick(10 * 60_000)
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
  })
})

describe('resident cards with trends', () => {
  it('every card has a sparkline and the last 5 readings as text for each of the 3 vitals', async () => {
    await renderLoaded()
    expect(residentCards()).toHaveLength(20)
    residentCards().forEach((card) => {
      expect(within(card).getAllByText(/^Last 5 readings, oldest first:/)).toHaveLength(3)
      expect(card.querySelectorAll('svg[aria-hidden="true"] polyline, svg[aria-hidden="true"] circle').length).toBeGreaterThanOrEqual(3)
    })
  })

  it('trend text lists the readings oldest first, in display units', async () => {
    await renderLoaded()
    const card = cardFor('Test Resident J')
    // HR 71 newest, wobble [0, 2, -1, 3, -2] newest first -> oldest first 69, 74, 70, 73, 71.
    expect(card.getByText('Last 5 readings, oldest first: 69 bpm, 74 bpm, 70 bpm, 73 bpm, 71 bpm.')).toBeInTheDocument()
    // Test Resident J's demo data has one missing SpO2 reading (the middle one).
    expect(card.getByText(/^Last 5 readings, oldest first: \d+%, \d+%, No reading, \d+%, \d+%\.$/)).toBeInTheDocument()
    expect(card.getByText(/^Last 5 readings, oldest first: (\d+\.\d °C(, )?){5}\.$/)).toBeInTheDocument()
  })

  it('trend text is visually hidden but available to screen readers', async () => {
    await renderLoaded()
    cardFor('Test Resident A')
      .getAllByText(/^Last 5 readings, oldest first:/)
      .forEach((el) => expect(el).toHaveClass('sr-only'))
  })

  it('says "No reading" in the trend for a missing value', async () => {
    const data = demoDataFor('staff')
    const [a, ...rest] = data.residents
    const missing = { ...a, vitals: a.vitals.map((v, i) => (i === 4 ? { ...v, spo2_pct: null } : v)) }
    await renderLoaded({ residents: [missing, ...rest], alerts: [] })
    expect(cardFor('Test Resident A').getByText(/^Last 5 readings, oldest first: No reading, \d+%/)).toBeInTheDocument()
  })

  it('resident cards show vitals, battery, wearable state, and last updated', async () => {
    await renderLoaded()
    const card = cardFor('Test Resident J')
    expect(card.getByText('res-0010')).toBeInTheDocument()
    expect(card.getByText('Heart rate').nextElementSibling).toHaveTextContent('71 bpm')
    expect(card.getByText('SpO2').nextElementSibling).toHaveTextContent('97%')
    expect(card.getByText('Skin temp').nextElementSibling).toHaveTextContent('33.6 °C')
    expect(card.getByText('Battery').nextElementSibling).toHaveTextContent('58%')
    expect(card.getByText('Not worn · Charging')).toBeInTheDocument()
    expect(card.getByText(/Last updated/)).toBeInTheDocument()
  })

  it('shows "No residents yet" when there are no residents', async () => {
    dashboardLoader.mockResolvedValue({ residents: [], alerts: [] })
    renderDoctor()
    expect(await screen.findByText('No residents yet')).toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(tileValue('Residents monitored')).toBe('0')
  })
})

describe('status comes from the data, never from vitals', () => {
  it('shows the provided status even when vitals look extreme or normal', async () => {
    const data = demoDataFor('staff')
    const [first, second, ...rest] = data.residents
    const extremeButOk = {
      ...first,
      status: 'ok' as const,
      vitals: first.vitals.map((v) => ({ ...v, hr_bpm: 200, spo2_pct: 60, skin_temp_c: 44.0 })),
    }
    const normalButUrgent = { ...second, status: 'urgent' as const }
    await renderLoaded({ residents: [extremeButOk, normalButUrgent, ...rest], alerts: [] })
    expect(cardFor('Test Resident A').getByText('OK')).toBeInTheDocument()
    expect(cardFor('Test Resident A').getByText('200 bpm')).toBeInTheDocument()
    expect(cardFor('Test Resident B').getByText('Urgent')).toBeInTheDocument()
  })

  it('shows the provided alert severity', async () => {
    const data = demoDataFor('staff')
    await renderLoaded({ ...data, alerts: [alertFor(3, { alert_type: 'fall', severity: 'warning' })] })
    expect(summaryRows()[0]).toHaveTextContent('Warning')
    expect(summaryRows()[0]).not.toHaveTextContent('Critical')
    expect(summaryRows()[0]).toHaveTextContent('Fall detected')
  })
})

describe('icon and text, never colour alone', () => {
  it('every resident status and alert severity badge has an icon and a text label', async () => {
    const { container } = await renderLoaded()
    const badges = container.querySelectorAll('[data-tone]')
    expect(badges).toHaveLength(20 + 3)
    const labels = { ok: 'OK', attention: 'Attention', urgent: 'Urgent', warning: 'Warning', critical: 'Critical' }
    badges.forEach((badge) => {
      const tone = badge.getAttribute('data-tone') as keyof typeof labels
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(badge).toHaveTextContent(labels[tone])
    })
  })
})

describe('search and filters', () => {
  const search = () => screen.getByRole('searchbox', { name: 'Search residents' })
  const chip = (name: RegExp) =>
    within(screen.getByRole('group', { name: 'Filter by status' })).getByRole('button', { name })

  it('searches by name', async () => {
    const { user } = await renderLoaded()
    await user.click(search())
    await user.paste('test resident c')
    expect(residentCards()).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 3, name: 'Test Resident C' })).toBeInTheDocument()
    expect(screen.getByText('Showing 1 of 20 residents')).toBeInTheDocument()
  })

  it('searches by resident ID', async () => {
    const { user } = await renderLoaded()
    await user.click(search())
    await user.paste('RES-0012')
    expect(residentCards()).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 3, name: 'Test Resident L' })).toBeInTheDocument()
  })

  it('filter chips show counts, are toggle buttons, and filter by status', async () => {
    const { user, data } = await renderLoaded()
    const count = (s: string) => data.residents.filter((r) => r.status === s).length

    expect(chip(/^All/)).toHaveAttribute('aria-pressed', 'true')
    expect(chip(/^All/)).toHaveTextContent(`All${data.residents.length}`)
    expect(chip(/^Urgent/)).toHaveTextContent(`Urgent${count('urgent')}`)
    expect(chip(/^Attention/)).toHaveTextContent(`Attention${count('attention')}`)
    expect(chip(/^OK/)).toHaveTextContent(`OK${count('ok')}`)

    await user.click(chip(/^Urgent/))
    expect(chip(/^Urgent/)).toHaveAttribute('aria-pressed', 'true')
    expect(chip(/^All/)).toHaveAttribute('aria-pressed', 'false')
    expect(residentCards()).toHaveLength(count('urgent'))
    residentCards().forEach((card) => expect(card).toHaveTextContent('Urgent'))
  })

  it('filtering keeps the trends on the cards that remain', async () => {
    const { user } = await renderLoaded()
    await user.click(chip(/^Urgent/))
    residentCards().forEach((card) => {
      expect(within(card).getAllByText(/^Last 5 readings, oldest first:/)).toHaveLength(3)
    })
  })

  it('shows the empty state when nothing matches, and Clear filters resets', async () => {
    const { user } = await renderLoaded()
    await user.click(chip(/^OK/))
    await user.click(search())
    await user.paste('res-0003') // Test Resident C is urgent, so not in OK
    expect(residentCards()).toHaveLength(0)
    expect(screen.getByText('No residents match your search')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(residentCards()).toHaveLength(20)
    expect(search()).toHaveValue('')
    expect(chip(/^All/)).toHaveAttribute('aria-pressed', 'true')
  })

  it('announces the result count politely', async () => {
    await renderLoaded()
    expect(screen.getByText('Showing 20 of 20 residents')).toHaveAttribute('aria-live', 'polite')
  })
})

// axe over a full dashboard is slow when the whole suite runs in parallel.
describe('accessibility', { timeout: 30_000 }, () => {
  it('has no axe violations', async () => {
    const { container } = await renderLoaded()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations in the loading state', async () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    const { container } = renderDoctor()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations in the empty state', async () => {
    dashboardLoader.mockResolvedValue({ residents: [], alerts: [] })
    const { container } = renderDoctor()
    await screen.findByText('No residents yet')
    expect(await axeViolations(container)).toEqual([])
  })

  it('headings are in order (h1 > h2 > h3)', async () => {
    await renderLoaded()
    const levels = screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))
    expect(levels[0]).toBe(1)
    levels.slice(1).forEach((level, i) => expect(level - levels[i]).toBeLessThanOrEqual(1))
  })
})

describe('staff area nav', () => {
  it('shows the "Staff area" tabs, with Dashboard as the current page', async () => {
    await renderLoaded()
    const nav = screen.getByRole('navigation', { name: 'Staff area' })
    expect(within(nav).getAllByRole('link').map((l) => l.textContent)).toEqual(['Dashboard', 'Devices'])
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Devices' })).toHaveAttribute('href', '/devices')
    expect(within(nav).getByRole('link', { name: 'Devices' })).not.toHaveAttribute('aria-current')
  })

  it('is shown in the loading state too', () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    renderDoctor()
    expect(screen.getByRole('navigation', { name: 'Staff area' })).toBeInTheDocument()
  })
})
