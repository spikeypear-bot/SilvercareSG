import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authMock, resetAuthMock } from '../../test/authMock'
import { resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS } from '../../test/fixtures'
import { renderApp, renderWithAuth } from '../../test/render'
import { resetResidentDetailLoader } from '../../test/residentMock'
import { DoctorDashboard } from './DoctorDashboard'
import { FamilyView } from './FamilyView'
import { NurseDashboard } from './NurseDashboard'
import type { DashboardView } from './useDashboardData'

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
    renderWithAuth(<FamilyView />, { user: TEST_USERS.family })
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
