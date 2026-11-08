/**
 * The nurse's alerts map (docs/features/alerts-map.md, criteria 13-20, test list in 23).
 * Leaflet runs for real in jsdom (no tiles load: fetch/images go nowhere), so pins are
 * checked as DOM markers inside the map region.
 */
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiAlert, apiResident } from '../../test/apiDashboard'
import { axeViolations } from '../../test/axe'
import { dashboardLoader, resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { DashboardData, Home } from '../../types/dashboard'
import { DoctorDashboard } from './DoctorDashboard'
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

beforeEach(() => {
  resetDashboardLoader()
})

const HOME_1: Home = { home_id: 'home-test-001', address: 'Test address 1 (synthetic)', lat: 1.36, lng: 103.8 }
const HOME_2: Home = { home_id: 'home-test-002', address: 'Test address 2 (synthetic)', lat: 1.37, lng: 103.81 }

/**
 * A and C share home-test-001 (A critical fall, C acknowledged SpO2 warning); B at
 * home-test-002 has a device problem (warning) and a resolved alert; D's home has no
 * location; E has no home_id; one unassigned device; F has only a cancelled alert.
 */
function mapData(overrides: Partial<DashboardData> = {}): DashboardData {
  const recent = new Date(Date.now() - 5 * 60_000).toISOString()
  return {
    residents: [
      apiResident(1),
      apiResident(2),
      apiResident(3, { home_id: 'home-test-001' }),
      apiResident(4),
      apiResident(5, { home_id: null }),
      apiResident(6),
    ].map(toResident),
    alerts: [
      apiAlert({ alert_id: 'alert-test-001', event_ts: recent }),
      apiAlert({
        alert_id: 'alert-test-002',
        resident_id: 'resident-test-002',
        device_id: 'wearable-test-002',
        alert_type: 'battery_low',
        severity: 'warning',
        event_ts: recent,
      }),
      apiAlert({
        alert_id: 'alert-test-003',
        resident_id: 'resident-test-003',
        alert_type: 'spo2_low',
        severity: 'warning',
        status: 'acknowledged',
        acknowledged_at: recent,
        event_ts: recent,
      }),
      apiAlert({ alert_id: 'alert-test-004', resident_id: 'resident-test-004', severity: 'warning', alert_type: 'hr_high' }),
      apiAlert({ alert_id: 'alert-test-005', resident_id: 'resident-test-005', severity: 'warning', alert_type: 'inactivity' }),
      apiAlert({
        alert_id: 'alert-test-006',
        resident_id: 'unassigned',
        device_id: 'wearable-test-050',
        alert_type: 'device_offline',
        severity: 'warning',
      }),
    ]
      .map(toResidentAlert)
      .concat(
        // Closed alerts (off the open list); GET /dashboard sends only open-list ones, so set the status after mapping.
        { ...toResidentAlert(apiAlert({ alert_id: 'alert-test-007', resident_id: 'resident-test-006' })), status: 'cancelled' },
        {
          ...toResidentAlert(apiAlert({ alert_id: 'alert-test-008', resident_id: 'resident-test-002', alert_type: 'hr_low' })),
          status: 'resolved',
        },
      ),
    homes: [HOME_1, HOME_2],
    ...overrides,
  }
}

async function renderNurse(data: DashboardData = mapData()) {
  dashboardLoader.mockResolvedValue(data)
  const user = userEvent.setup()
  const view = renderWithAuth(<NurseDashboard />, { user: TEST_USERS.nurse })
  await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
  return { user, ...view }
}

const inbox = () => screen.getByRole('region', { name: /^Open alerts/ })
const toggleGroup = () => within(inbox()).getByRole('group', { name: 'Show alerts as' })
const listButton = () => within(toggleGroup()).getByRole('button', { name: 'List' })
const mapButton = () => within(toggleGroup()).getByRole('button', { name: 'Map' })
const map = () => screen.getByRole('region', { name: 'Map of homes with open alerts' })
const pins = () => Array.from(map().querySelectorAll<HTMLElement>('.leaflet-marker-icon'))
const pinFor = (homeId: string) => {
  const pin = pins().find((p) => p.textContent?.includes(homeId))
  if (!pin) throw new Error(`no pin for ${homeId}`)
  return pin
}
const details = (homeId: string) => screen.getByRole('region', { name: homeId })

async function openMap(user: ReturnType<typeof userEvent.setup>) {
  await user.click(mapButton())
  await waitFor(() => expect(pins().length).toBeGreaterThan(0))
}

describe('List / Map toggle (criterion 13)', () => {
  it('defaults to List, with aria-pressed on the active button', async () => {
    await renderNurse()
    expect(listButton()).toHaveAttribute('aria-pressed', 'true')
    expect(mapButton()).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('region', { name: 'Map of homes with open alerts' })).not.toBeInTheDocument()
  })

  it('switches to the map and back to the list', async () => {
    const { user } = await renderNurse()
    await user.click(mapButton())
    expect(mapButton()).toHaveAttribute('aria-pressed', 'true')
    expect(listButton()).toHaveAttribute('aria-pressed', 'false')
    expect(map()).toBeInTheDocument()
    await user.click(listButton())
    expect(listButton()).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('region', { name: 'Map of homes with open alerts' })).not.toBeInTheDocument()
    expect(within(inbox()).getByRole('button', { name: 'Acknowledge: Fall detected, Test Resident A' })).toBeInTheDocument()
  })

  it('works with the keyboard', async () => {
    const { user } = await renderNurse()
    mapButton().focus()
    await user.keyboard('{Enter}')
    expect(mapButton()).toHaveAttribute('aria-pressed', 'true')
    listButton().focus()
    await user.keyboard(' ')
    expect(listButton()).toHaveAttribute('aria-pressed', 'true')
  })

  it('the doctor dashboard has no toggle and no map', async () => {
    dashboardLoader.mockResolvedValue(mapData())
    renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
    await screen.findByRole('heading', { level: 2, name: 'Residents' })
    expect(screen.queryByRole('group', { name: 'Show alerts as' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Map' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Map of homes with open alerts' })).not.toBeInTheDocument()
  })
})

describe('states (criterion 18)', () => {
  it('while loading: the existing loading state, no toggle or map', async () => {
    const pending = deferred<DashboardData>()
    dashboardLoader.mockReturnValue(pending.promise)
    renderWithAuth(<NurseDashboard />, { user: TEST_USERS.nurse })
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Show alerts as' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Map of homes with open alerts' })).not.toBeInTheDocument()
    await act(async () => pending.resolve(mapData()))
  })

  it('no open-list alerts: the existing empty state, no toggle and no map', async () => {
    const data = mapData()
    await renderNurse({
      ...data,
      alerts: data.alerts.filter((a) => a.status === 'resolved' || a.status === 'cancelled'),
      homes: [],
    })
    expect(within(inbox()).getByText('No open alerts.')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Show alerts as' })).not.toBeInTheDocument()
    expect(document.querySelector('.leaflet-container')).toBeNull()
  })

  it('homes_error: the notice above the map, and every open alert listed under it', async () => {
    const { user } = await renderNurse(mapData({ homes: [], homes_error: true }))
    await user.click(mapButton())
    const notice = within(inbox()).getByText("Couldn't load home locations. The alert list still works.")
    // Above the map in document order.
    expect(notice.compareDocumentPosition(map()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(pins()).toHaveLength(0)
    for (const line of [
      'Test Resident A (home-test-001): no location on file',
      'Test Resident B (home-test-002): no location on file',
      'Test Resident C (home-test-001): no location on file',
      'Test Resident D (home-test-004): no location on file',
      'Test Resident E (no home ID): no location on file',
      'Unassigned device wearable-test-050: no home',
    ]) {
      expect(within(inbox()).getByText(line)).toBeInTheDocument()
    }
  })

  it('no homes_error notice when the home read worked', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    expect(screen.queryByText(/Couldn't load home locations/)).not.toBeInTheDocument()
  })
})

describe('pins (criteria 14-15)', () => {
  it('one pin per home with open-list alerts, worst severity as text', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    expect(pins()).toHaveLength(2)
    // home-test-001: A's critical fall + C's warning -> Critical; home-test-002: B's device problem -> Warning.
    expect(pinFor('home-test-001')).toHaveTextContent(/^.?Critical, home-test-001, Test address 1 \(synthetic\), 2 alerts$/)
    expect(pinFor('home-test-002')).toHaveTextContent(/^.?Warning, home-test-002, Test address 2 \(synthetic\), 1 alert$/)
  })

  it('the severity icon is decorative; the visible label is text', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    const pin = pinFor('home-test-001')
    expect(within(pin).getByText('Critical')).toBeVisible()
    expect(pin.querySelector('[aria-hidden="true"]')).not.toBeNull()
  })

  it('pins are keyboard-focusable buttons', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    for (const pin of pins()) {
      expect(pin).toHaveAttribute('tabindex', '0')
      expect(pin).toHaveAttribute('role', 'button')
    }
  })

  it('a home in homes with no open-list alert gets no pin', async () => {
    const extra: Home = { home_id: 'home-test-006', address: 'Test address 6 (synthetic)', lat: 1.365, lng: 103.805 }
    const { user } = await renderNurse(mapData({ homes: [HOME_1, HOME_2, extra] }))
    await openMap(user)
    expect(pins().map((p) => p.textContent)).not.toContainEqual(expect.stringContaining('home-test-006'))
  })

  it('shows the OneMap attribution with a link to OneMap', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    const attribution = map().querySelector('.leaflet-control-attribution') as HTMLElement
    expect(attribution).toHaveTextContent('OneMap © contributors | Singapore Land Authority')
    expect(within(attribution).getByRole('link', { name: 'OneMap' })).toHaveAttribute('href', 'https://www.onemap.gov.sg/')
    expect(attribution.querySelector('img[src*="onemap.gov.sg"]')).not.toBeNull()
  })

  it('uses the OneMap tile URL', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    await waitFor(() => expect(map().querySelector('img.leaflet-tile')).not.toBeNull())
    const tile = map().querySelector('img.leaflet-tile') as HTMLImageElement
    expect(tile.src).toMatch(/^https:\/\/www\.onemap\.gov\.sg\/maps\/tiles\/Default\/\d+\/\d+\/\d+\.png$/)
  })
})

describe('pin details (criterion 16)', () => {
  it('a click shows each resident of the home with address, alerts, status, time, and View resident link', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    fireEvent.click(pinFor('home-test-001'))
    const section = await screen.findByRole('region', { name: 'home-test-001' })
    const residents = within(section).getAllByRole('link', { name: 'View resident' })
    expect(residents.map((a) => a.getAttribute('href'))).toEqual([
      '/residents/resident-test-001',
      '/residents/resident-test-003',
    ])
    expect(within(section).getByText('Test Resident A')).toBeInTheDocument()
    expect(within(section).getByText('Test Resident C')).toBeInTheDocument()
    expect(within(section).getAllByText('Test address 1 (synthetic)')).toHaveLength(2)
    expect(within(section).getByText('Fall detected, Test Resident A')).toBeInTheDocument()
    expect(within(section).getByText('Low blood oxygen, Test Resident C')).toBeInTheDocument()
    expect(within(section).getByText('Open')).toBeInTheDocument()
    expect(within(section).getByText('Acknowledged')).toBeInTheDocument()
    expect(within(section).getAllByText(/5 min/)).toHaveLength(2)
    // Other homes' residents and closed alerts are not in this home's details.
    expect(within(section).queryByText(/Test Resident B/)).not.toBeInTheDocument()
  })

  it('moves focus to the details heading', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    fireEvent.click(pinFor('home-test-002'))
    const heading = await screen.findByRole('heading', { level: 3, name: 'home-test-002' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(within(details('home-test-002')).getByText('Low battery, Test Resident B')).toBeInTheDocument()
    expect(within(details('home-test-002')).queryByText(/Low heart rate/)).not.toBeInTheDocument()
  })

  it('Enter on a focused pin opens its details', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    pinFor('home-test-002').focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('region', { name: 'home-test-002' })).toBeInTheDocument()
  })

  it('Space on a focused pin opens its details', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    pinFor('home-test-001').focus()
    await user.keyboard(' ')
    expect(await screen.findByRole('region', { name: 'home-test-001' })).toBeInTheDocument()
  })
})

describe('lines under the map (criterion 17)', () => {
  it('lists residents with no location and unassigned devices, not those with a pin', async () => {
    const { user } = await renderNurse()
    await openMap(user)
    expect(within(inbox()).getByText('Test Resident D (home-test-004): no location on file')).toBeInTheDocument()
    expect(within(inbox()).getByText('Test Resident E (no home ID): no location on file')).toBeInTheDocument()
    expect(within(inbox()).getByText('Unassigned device wearable-test-050: no home')).toBeInTheDocument()
    expect(within(inbox()).queryByText(/Test Resident A .*no location/)).not.toBeInTheDocument()
    // F has only a cancelled alert: off the open list, so nowhere on the map.
    expect(within(inbox()).queryByText(/Test Resident F/)).not.toBeInTheDocument()
  })
})

describe('polling (criterion 19)', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('updates pins on refresh, keeping the existing marker element', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { user } = await renderNurse()
    await openMap(user)
    const first = pinFor('home-test-001')
    // Next poll: B's device problem is resolved, so home-test-002 loses its pin.
    const data = mapData()
    dashboardLoader.mockResolvedValue({
      ...data,
      alerts: data.alerts.filter((a) => a.alert_id !== 'alert-test-002'),
      homes: [HOME_1],
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    await waitFor(() => expect(pins()).toHaveLength(1))
    expect(pinFor('home-test-001')).toBe(first)
  })
})

describe('accessibility', { timeout: 30_000 }, () => {
  it('map view with details open has no axe violations', async () => {
    const { user, container } = await renderNurse()
    await openMap(user)
    fireEvent.click(pinFor('home-test-001'))
    await screen.findByRole('region', { name: 'home-test-001' })
    expect(await axeViolations(container)).toEqual([])
  })
})
