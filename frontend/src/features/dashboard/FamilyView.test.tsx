import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { dashboardLoader, demoDataFor, resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { DashboardData, ResidentAlert } from '../../types/dashboard'
import { FamilyView } from './FamilyView'
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

function renderFamily() {
  const user = userEvent.setup()
  const view = renderWithAuth(<FamilyView />, { user: TEST_USERS.family })
  return { user, ...view }
}

async function renderLoaded(data: DashboardData = demoDataFor('family')) {
  dashboardLoader.mockResolvedValue(data)
  const ctx = renderFamily()
  await screen.findAllByRole('article')
  return { ...ctx, data }
}

const residentCard = (name: string) =>
  within(screen.getByRole('heading', { level: 2, name }).closest('article') as HTMLElement)

describe('loading, empty, error', () => {
  it('shows skeleton cards with a loading status, plus header, banner, and greeting', () => {
    dashboardLoader.mockReturnValue(deferred<DashboardData>().promise)
    renderFamily()
    expect(screen.getByRole('status')).toHaveTextContent("Loading your family's information")
    expect(screen.getByRole('heading', { level: 1, name: 'My family' })).toBeInTheDocument()
    expect(screen.getByRole('note', { name: 'Demo data' })).toHaveTextContent(BANNER)
    expect(screen.getByText(/^Good (morning|afternoon|evening), Test Family A$/)).toBeInTheDocument()
    expect(dashboardLoader).toHaveBeenCalledWith('family')
  })

  it('shows the empty state when no residents are linked', async () => {
    dashboardLoader.mockResolvedValue({ residents: [], alerts: [] })
    renderFamily()
    expect(
      await screen.findByText(
        'No residents are linked to your account yet. Your care provider can link you.',
      ),
    ).toBeInTheDocument()
  })

  it('shows the error notice with Try again, which reloads', async () => {
    dashboardLoader.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderFamily()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load the dashboard.")
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findAllByRole('article')).toHaveLength(2)
  })
})

describe('linked residents (criterion 21)', () => {
  it('shows the 2 linked demo residents', async () => {
    await renderLoaded()
    expect(screen.getAllByRole('article')).toHaveLength(2)
    expect(screen.getByRole('heading', { level: 2, name: 'Test Resident B' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Test Resident E' })).toBeInTheDocument()
  })

  it('shows staff-only parts nowhere', async () => {
    await renderLoaded()
    expect(screen.queryByText('Residents monitored')).not.toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Filter by status' })).not.toBeInTheDocument()
    expect(screen.queryByText('Test Resident A')).not.toBeInTheDocument()
  })

  it('each card shows a status badge with the provided sentence', async () => {
    await renderLoaded()
    expect(residentCard('Test Resident B').getByText('Attention')).toBeInTheDocument()
    expect(residentCard('Test Resident B').getByText('Needs attention')).toBeInTheDocument()
    expect(residentCard('Test Resident E').getByText('OK')).toBeInTheDocument()
    expect(residentCard('Test Resident E').getByText('All readings look normal')).toBeInTheDocument()
  })

  it('shows four vitals tiles, each with a sparkline, the last 5 readings as text, and the time', async () => {
    const { container } = await renderLoaded()
    const card = residentCard('Test Resident E')
    for (const label of ['Heart rate', 'SpO2', 'Skin temperature', 'Activity']) {
      expect(card.getByText(label)).toBeInTheDocument()
    }
    expect(card.getByText('70 bpm')).toBeInTheDocument()
    expect(card.getByText('34.0 °C')).toBeInTheDocument()
    expect(card.getAllByText(/^Last 5 readings, oldest first:/)).toHaveLength(4)
    expect(card.getAllByText(/^At/)).toHaveLength(4)
    // Sparklines are decorative SVGs (aria-hidden), one per tile.
    const sparklines = container.querySelectorAll('svg[aria-hidden="true"] polyline')
    expect(sparklines.length).toBeGreaterThanOrEqual(8)
  })

  it('shows wearable battery, worn state, and bedroom sensor status', async () => {
    await renderLoaded()
    const card = residentCard('Test Resident B')
    const wearable = card.getByText('Wearable').nextElementSibling as HTMLElement
    expect(wearable).toHaveTextContent('64%')
    expect(wearable).toHaveTextContent('Worn')
    expect(card.getByText('Bedroom sensor').nextElementSibling).toHaveTextContent('Online')
  })

  it('shows Offline as text for an offline bedroom sensor', async () => {
    const data = demoDataFor('family')
    data.residents[0] = { ...data.residents[0], bedroom: { ...data.residents[0].bedroom, online: false } }
    await renderLoaded(data)
    expect(residentCard('Test Resident B').getByText('Bedroom sensor').nextElementSibling).toHaveTextContent(
      'Offline',
    )
  })
})

describe('recent alerts timeline', () => {
  it("shows each resident's own alerts, newest first, with severity and type", async () => {
    await renderLoaded()
    const items = within(
      residentCard('Test Resident B').getByText('Recent alerts').nextElementSibling as HTMLElement,
    ).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Warning')
    expect(items[0]).toHaveTextContent('Inactivity')
    expect(items[1]).toHaveTextContent('Info')
    expect(items[1]).toHaveTextContent('Possible fall')
    expect(residentCard('Test Resident E').getByText('No recent alerts.')).toBeInTheDocument()
  })

  it('shows at most 5 alerts per resident', async () => {
    const data = demoDataFor('family')
    const base = data.alerts[0]
    const many: ResidentAlert[] = Array.from({ length: 7 }, (_, i) => ({
      ...base,
      alert_id: `alert-test-${i}`,
      resident_id: 'res-0002',
      event_ts: new Date(Date.now() - i * 60_000).toISOString(),
    }))
    await renderLoaded({ ...data, alerts: many })
    const list = residentCard('Test Resident B').getByText('Recent alerts').nextElementSibling as HTMLElement
    expect(within(list).getAllByRole('listitem')).toHaveLength(5)
  })

  it('the timeline is inside an aria-live="polite" region (criterion 22)', async () => {
    await renderLoaded()
    const region = residentCard('Test Resident B').getByText('Recent alerts').nextElementSibling
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(within(region as HTMLElement).getByRole('list')).toBeInTheDocument()
  })
})

describe('status comes from the data (criteria 19, 22)', () => {
  it('shows the provided status, not one computed from vitals', async () => {
    const data = demoDataFor('family')
    const [b, e] = data.residents
    const extremeButOk = {
      ...b,
      status: 'ok' as const,
      status_text: 'All readings look normal',
      vitals: b.vitals.map((v) => ({ ...v, hr_bpm: 220, spo2_pct: 55 })),
    }
    const normalButUrgent = { ...e, status: 'urgent' as const, status_text: 'Urgent: check now' }
    await renderLoaded({ residents: [extremeButOk, normalButUrgent], alerts: [] })
    expect(residentCard('Test Resident B').getByText('OK')).toBeInTheDocument()
    expect(residentCard('Test Resident B').getByText('220 bpm')).toBeInTheDocument()
    expect(residentCard('Test Resident E').getByText('Urgent')).toBeInTheDocument()
    expect(residentCard('Test Resident E').getByText('Urgent: check now')).toBeInTheDocument()
  })

  it('every badge has an icon and a text label', async () => {
    const { container } = await renderLoaded()
    const badges = container.querySelectorAll('[data-tone]')
    expect(badges.length).toBeGreaterThan(2)
    badges.forEach((badge) => {
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(badge.textContent?.trim()).not.toBe('')
    })
  })

  it('shows "No reading" for a missing SpO2 value', async () => {
    const data = demoDataFor('family')
    const [b, e] = data.residents
    const missing = { ...b, vitals: b.vitals.map((v, i) => (i === 0 ? { ...v, spo2_pct: null } : v)) }
    await renderLoaded({ residents: [missing, e], alerts: [] })
    expect(residentCard('Test Resident B').getByText('No reading')).toBeInTheDocument()
  })
})

// axe over a full dashboard is slow when the whole suite runs in parallel.
describe('accessibility', { timeout: 30_000 }, () => {
  it('has no axe violations', async () => {
    const { container } = await renderLoaded()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations in the empty state', async () => {
    dashboardLoader.mockResolvedValue({ residents: [], alerts: [] })
    const { container } = renderFamily()
    await screen.findByText(/No residents are linked/)
    expect(await axeViolations(container)).toEqual([])
  })

  it('headings are in order (h1 > h2 > h3)', async () => {
    await renderLoaded()
    const levels = screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))
    expect(levels[0]).toBe(1)
    levels.slice(1).forEach((level, i) => expect(level - levels[i]).toBeLessThanOrEqual(1))
  })
})
