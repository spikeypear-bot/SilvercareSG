import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiResident } from '../../test/apiDashboard'
import { authMock, resetAuthMock } from '../../test/authMock'
import { axeViolations } from '../../test/axe'
import { resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS } from '../../test/fixtures'
import { renderApp, renderWithAuth } from '../../test/render'
import { resetResidentDetailLoader } from '../../test/residentMock'
import { DoctorDashboard } from './DoctorDashboard'
import { CaregiverView } from './CaregiverView'
import { NurseDashboard } from './NurseDashboard'
import { ResidentCard } from './ResidentCard'
import { toResident, type DashboardView } from './useDashboardData'

vi.mock('../../lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/auth')>()
  const { authMock } = await import('../../test/authMock')
  return { ...actual, ...authMock }
})

vi.mock('./useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./useDashboardData')>()
  const { dashboardLoader } = await import('../../test/dashboardMock')
  const { residentDetailLoader } = await import('../../test/residentMock')
  return {
    ...actual,
    useDashboardData: (view: DashboardView) => actual.useDashboardData(view, dashboardLoader),
    useResidentDetail: (id: string) => actual.useResidentDetail(id, residentDetailLoader),
  }
})

beforeEach(() => {
  resetAuthMock()
  resetDashboardLoader()
  resetResidentDetailLoader()
})

const tileGroup = () => screen.getByRole('group', { name: 'Choose what the panel below shows' })
const tile = (label: string) => within(tileGroup()).getByRole('button', { name: new RegExp(`^${label}`) })
const location = () => screen.getByTestId('location').textContent

function expectCardLinks() {
  const cards = screen.getAllByRole('article')
  expect(cards).toHaveLength(20)
  cards.forEach((card, i) => {
    const id = `res-${String(i + 1).padStart(4, '0')}`
    const name = `Test Resident ${'ABCDEFGHIJKLMNOPQRST'[i]}`
    const links = within(card).getAllByRole('link')
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAccessibleName(name)
    expect(links[0]).toHaveAttribute('href', `/residents/${id}`)
    expect(links[0].getAttribute('href')).not.toMatch(/Test|Resident|%20/)
    // The heading keeps the resident name as its whole name.
    expect(within(card).getByRole('heading', { level: 3 })).toHaveAccessibleName(name)
    expect(within(card).getByRole('heading', { level: 3, name })).toContainElement(links[0])
  })
}

describe('ResidentCard link to the resident page', () => {
  it('doctor dashboard: every card links to /residents/<opaque id> named by the resident', async () => {
    renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
    await screen.findByRole('heading', { level: 2, name: 'Residents' })
    expectCardLinks()
  })

  it('nurse dashboard residents panel: every card links to /residents/<opaque id>', async () => {
    const user = userEvent.setup()
    renderWithAuth(<NurseDashboard />, { user: TEST_USERS.nurse })
    await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
    await user.click(tile('Residents monitored'))
    expectCardLinks()
  })

  it('family cards do not link to the staff-only resident page', async () => {
    renderWithAuth(<CaregiverView />, { user: TEST_USERS.caregiver })
    expect(await screen.findAllByRole('article')).toHaveLength(2)
    screen.getAllByRole('article').forEach((card) => {
      within(card)
        .queryAllByRole('link')
        .forEach((link) => expect(link.getAttribute('href') ?? '').not.toMatch(/^\/residents\//))
    })
  })

  it('doctor: Enter on the focused card link opens the resident page', async () => {
    const user = userEvent.setup()
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.doctor)
    renderApp('/')
    const link = await screen.findByRole('link', { name: 'Test Resident C' })
    link.focus()
    expect(link).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('heading', { level: 2, name: 'Test Resident C' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Resident details' })).toBeInTheDocument()
    expect(location()).toBe('/residents/res-0003')
    // Focus goes to the top of the new page.
    await waitFor(() => expect(screen.getByRole('link', { name: 'Back to dashboard' })).toHaveFocus())
    await waitFor(() => expect(document.title).toBe('SilverCare - Resident details'))
  })

  it('nurse: Tab to a card link and press Enter, then Back to dashboard returns', async () => {
    const user = userEvent.setup()
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.nurse)
    renderApp('/')
    await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
    await user.click(tile('Residents monitored'))
    const link = screen.getByRole('link', { name: 'Test Resident A' })
    // Tab from the search box (just before the cards) until the first card link has focus.
    screen.getByRole('searchbox', { name: 'Search residents' }).focus()
    for (let i = 0; i < 20 && document.activeElement !== link; i++) await user.tab()
    expect(link).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('heading', { level: 2, name: 'Test Resident A' })).toBeInTheDocument()
    expect(location()).toBe('/residents/res-0001')

    await user.click(screen.getByRole('link', { name: 'Back to dashboard' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Nurse dashboard' })).toBeInTheDocument()
    expect(location()).toBe('/')
  })
})

describe('ResidentCard with missing devices and readings (GET /dashboard nulls)', () => {
  const NOW = new Date('2026-11-07T02:10:00Z')
  const card = () => screen.getByRole('article')
  const metric = (label: string) => within(card()).getByText(label, { selector: 'dt' }).nextElementSibling

  it('no wearable, no bedroom unit, no vitals: "No wearable assigned", battery Unknown, "No readings yet"', async () => {
    const resident = toResident(apiResident(2, { wearable: null, bedroom: null, vitals: [] }))
    const { container } = renderWithAuth(<ResidentCard resident={resident} now={NOW} />)
    expect(within(card()).getByRole('heading', { level: 3, name: 'Test Resident B' })).toBeInTheDocument()
    expect(card()).toHaveTextContent('No wearable assigned')
    expect(metric('Battery')).toHaveTextContent(/^Unknown$/)
    for (const label of ['Heart rate', 'SpO2', 'Skin temp']) expect(metric(label)).toHaveTextContent(/^No reading$/)
    expect(card()).toHaveTextContent('No readings yet')
    expect(card()).not.toHaveTextContent(/Last updated|NaN|undefined|null/)
    expect(await axeViolations(container)).toEqual([])
  })

  it('a wearable that has never reported: "Never reported", battery Unknown, "No readings yet"', async () => {
    const resident = toResident(
      apiResident(3, {
        wearable: { ts: null, battery_pct: null, worn: null, online: false },
        bedroom: { ts: null, online: false },
        vitals: [],
      }),
    )
    const { container } = renderWithAuth(<ResidentCard resident={resident} now={NOW} />)
    expect(card()).toHaveTextContent('Offline · Never reported')
    expect(metric('Battery')).toHaveTextContent(/^Unknown$/)
    expect(card()).toHaveTextContent('No readings yet')
    expect(card()).not.toHaveTextContent(/Last updated|NaN|undefined|null/)
    expect(await axeViolations(container)).toEqual([])
  })

  it('vitals but no wearable: "Last updated" from the vitals reading', () => {
    const resident = toResident(
      apiResident(4, {
        wearable: null,
        vitals: [{ ts: '2026-11-07T02:05:00Z', hr_bpm: 70, spo2_pct: 96, skin_temp_c: 33 }],
      }),
    )
    renderWithAuth(<ResidentCard resident={resident} now={NOW} />)
    expect(card()).toHaveTextContent('No wearable assigned')
    expect(metric('Heart rate')).toHaveTextContent('70 bpm')
    expect(card()).toHaveTextContent(/Last updated/)
    expect(card()).not.toHaveTextContent('No readings yet')
  })

  it('a reported wearable but no vitals: "Last updated" from the wearable status', () => {
    const resident = toResident(apiResident(5, { vitals: [] }))
    renderWithAuth(<ResidentCard resident={resident} now={NOW} />)
    expect(metric('Battery')).toHaveTextContent('80%')
    expect(card()).toHaveTextContent(/Last updated/)
    expect(card()).not.toHaveTextContent('No readings yet')
  })

  it('with trends on (doctor view) and no vitals, renders without a sparkline and without crashing', () => {
    const resident = toResident(apiResident(2, { wearable: null, bedroom: null, vitals: [] }))
    renderWithAuth(<ResidentCard resident={resident} now={NOW} showTrends />)
    expect(card()).toHaveTextContent('No readings yet')
    // The card's sparklines are 96 x 28 (ResidentCard); icons are other sizes.
    expect(card().querySelector('svg[viewBox="0 0 96 28"]')).toBeNull()
  })
})
