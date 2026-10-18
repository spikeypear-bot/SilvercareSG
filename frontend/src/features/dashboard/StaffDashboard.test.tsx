import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { dashboardLoader, demoDataFor, resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { DashboardData } from '../../types/dashboard'
import { StaffDashboard } from './StaffDashboard'
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

function renderStaff() {
  const user = userEvent.setup()
  const view = renderWithAuth(<StaffDashboard />, { user: TEST_USERS.doctor })
  return { user, ...view }
}

async function renderLoaded(data: DashboardData = demoDataFor('staff')) {
  dashboardLoader.mockResolvedValue(data)
  const ctx = renderStaff()
  await screen.findByRole('heading', { level: 2, name: 'Residents' })
  return { ...ctx, data }
}

const residentCards = () => screen.queryAllByRole('article')
const alertsPanel = () =>
  screen.getByRole('heading', { level: 2, name: /Open alerts/ }).closest('section') as HTMLElement
const tileValue = (label: string) =>
  screen.getByText(label, { selector: 'p' }).nextElementSibling?.textContent

describe('loading, error, and layout', () => {
  it('shows skeletons with a loading status, plus header, banner, and greeting', () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    renderStaff()
    expect(screen.getByRole('status')).toHaveTextContent('Loading dashboard')
    expect(screen.getByRole('heading', { level: 1, name: 'Staff dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('note', { name: 'Demo data' })).toHaveTextContent(BANNER)
    expect(screen.getByText(/^Good (morning|afternoon|evening), Test Doctor A$/)).toBeInTheDocument()
    expect(residentCards()).toHaveLength(0)
    expect(dashboardLoader).toHaveBeenCalledWith('staff')
  })

  it('shows an error notice with Try again, which reloads', async () => {
    dashboardLoader.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderStaff()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load the dashboard.")
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Residents' })).toBeInTheDocument()
    expect(dashboardLoader).toHaveBeenCalledTimes(2)
  })

  it('keeps the demo banner visible in the error state', async () => {
    dashboardLoader.mockRejectedValue(new Error('boom'))
    renderStaff()
    await screen.findByRole('alert')
    expect(screen.getByRole('note', { name: 'Demo data' })).toHaveTextContent(BANNER)
  })

  it('page title has no personal data', () => {
    renderStaff()
    expect(document.title).toBe('SilverCare - Staff dashboard')
  })
})

describe('content (criterion 21)', () => {
  it('shows all 20 demo residents', async () => {
    await renderLoaded()
    expect(residentCards()).toHaveLength(20)
    expect(screen.getByText('Showing 20 of 20 residents')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Test Resident A' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Test Resident T' })).toBeInTheDocument()
  })

  it('stat tiles match the data', async () => {
    const { data } = await renderLoaded()
    const critical = data.alerts.filter((a) => a.severity === 'critical').length
    const offline = data.residents.reduce(
      (n, r) => n + Number(!r.wearable.online) + Number(!r.bedroom.online),
      0,
    )
    const low = data.residents.filter((r) => r.wearable.battery_pct < 20).length

    expect(tileValue('Residents monitored')).toBe('20')
    expect(tileValue('Open alerts')).toBe(String(data.alerts.length))
    expect(screen.getByText(`${critical} critical`)).toBeInTheDocument()
    expect(tileValue('Devices offline')).toBe(String(offline))
    expect(tileValue('Wearables low on battery')).toBe(String(low))
    expect(screen.getByText('Below 20%')).toBeInTheDocument()
  })

  it('lists every open alert, newest first, with type, resident name and ID, and time', async () => {
    const { data } = await renderLoaded()
    const items = within(alertsPanel()).getAllByRole('listitem')
    expect(items).toHaveLength(data.alerts.length)

    // First (newest) demo alert is the fall for Test Resident C.
    expect(items[0]).toHaveTextContent('Critical')
    expect(items[0]).toHaveTextContent('Fall detected')
    expect(items[0]).toHaveTextContent('Test Resident C')
    expect(items[0]).toHaveTextContent('res-0003')
    expect(within(items[0]).getByText(/min ago/).closest('time')).toHaveAttribute('datetime')

    const times = items.map((li) => li.querySelector('time')?.getAttribute('dateTime') ?? '')
    expect([...times].sort().reverse()).toEqual(times)
  })

  it('sorts alerts newest first even if the data arrives oldest first', async () => {
    const data = demoDataFor('staff')
    await renderLoaded({ ...data, alerts: [...data.alerts].reverse() })
    const times = within(alertsPanel())
      .getAllByRole('listitem')
      .map((li) => li.querySelector('time')?.getAttribute('dateTime') ?? '')
    expect([...times].sort().reverse()).toEqual(times)
  })

  it('shows "No open alerts." when there are none', async () => {
    await renderLoaded({ ...demoDataFor('staff'), alerts: [] })
    expect(within(alertsPanel()).getByText('No open alerts.')).toBeInTheDocument()
    expect(tileValue('Open alerts')).toBe('0')
  })

  it('resident cards show vitals, battery, wearable state, and last updated', async () => {
    await renderLoaded()
    const card = screen.getByRole('heading', { level: 3, name: 'Test Resident J' }).closest('article') as HTMLElement
    const c = within(card)
    expect(c.getByText('res-0010')).toBeInTheDocument()
    expect(c.getByText('Heart rate').nextElementSibling).toHaveTextContent('71 bpm')
    expect(c.getByText('SpO2').nextElementSibling).toHaveTextContent('97%')
    expect(c.getByText('Skin temp').nextElementSibling).toHaveTextContent('33.6 °C')
    expect(c.getByText('Battery').nextElementSibling).toHaveTextContent('58%')
    expect(c.getByText('Not worn · Charging')).toBeInTheDocument()
    expect(c.getByText(/Last updated/)).toBeInTheDocument()
  })
})

describe('status comes from the data, never from vitals (criterion 19)', () => {
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

    const cardFor = (name: string) =>
      within(screen.getByRole('heading', { level: 3, name }).closest('article') as HTMLElement)
    expect(cardFor('Test Resident A').getByText('OK')).toBeInTheDocument()
    expect(cardFor('Test Resident A').getByText('200 bpm')).toBeInTheDocument()
    expect(cardFor('Test Resident B').getByText('Urgent')).toBeInTheDocument()
  })

  it('shows the provided alert severity', async () => {
    const data = demoDataFor('staff')
    const alert = { ...data.alerts[0], alert_type: 'fall' as const, severity: 'info' as const }
    await renderLoaded({ ...data, alerts: [alert] })
    const item = within(alertsPanel()).getByRole('listitem')
    expect(item).toHaveTextContent('Info')
    expect(item).toHaveTextContent('Fall detected')
  })
})

describe('icon and text, never colour alone (criterion 22)', () => {
  it('every resident status and alert severity badge has an icon and a text label', async () => {
    const { container } = await renderLoaded()
    const badges = container.querySelectorAll('[data-tone]')
    expect(badges.length).toBe(20 + demoDataFor('staff').alerts.length)
    const labels = { ok: 'OK', attention: 'Attention', urgent: 'Urgent', info: 'Info', warning: 'Warning', critical: 'Critical' }
    badges.forEach((badge) => {
      const tone = badge.getAttribute('data-tone') as keyof typeof labels
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(badge).toHaveTextContent(labels[tone])
    })
  })

  it('the alerts list is inside an aria-live="polite" region', async () => {
    await renderLoaded()
    const list = within(alertsPanel()).getByRole('list')
    expect(list.closest('[aria-live]')).toHaveAttribute('aria-live', 'polite')
  })

  it('"No open alerts." is also inside the live region', async () => {
    await renderLoaded({ ...demoDataFor('staff'), alerts: [] })
    expect(within(alertsPanel()).getByText('No open alerts.').closest('[aria-live]')).toHaveAttribute(
      'aria-live',
      'polite',
    )
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
    const { container } = renderStaff()
    expect(await axeViolations(container)).toEqual([])
  })

  it('headings are in order (h1 > h2 > h3)', async () => {
    await renderLoaded()
    const levels = screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))
    expect(levels[0]).toBe(1)
    levels.slice(1).forEach((level, i) => expect(level - levels[i]).toBeLessThanOrEqual(1))
  })
})
