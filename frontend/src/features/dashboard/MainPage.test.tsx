import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import { flushPromises, trackUnhandledRejections } from '../../test/unhandled'
import type { AuthUser } from '../../types/auth'
import { MainPage } from './MainPage'
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

const withGroups = (groups: string[]): AuthUser => ({ ...TEST_USERS.nurse, groups })

describe('view by group (Users and access, criterion 10)', () => {
  it.each([
    ['nurse', 'Nurse dashboard', 'Nurse', 'Doctor dashboard'],
    ['doctor', 'Doctor dashboard', 'Doctor', 'Nurse dashboard'],
  ] as const)('%s sees "%s" with role badge "%s"', async (key, heading, role, other) => {
    renderWithAuth(<MainPage />, { user: TEST_USERS[key] })
    expect(screen.getByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
    expect(within(screen.getByRole('button', { name: /user menu/i })).getByText(role)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: other })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'My family' })).not.toBeInTheDocument()
    await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
  })

  it('nurse gets the alert inbox with actions; doctor gets the review view without them', async () => {
    const first = renderWithAuth(<MainPage />, { user: TEST_USERS.nurse })
    await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
    expect(screen.getByRole('group', { name: 'Choose what the panel below shows' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^Acknowledge: / }).length).toBeGreaterThan(0)
    expect(screen.queryAllByRole('article')).toHaveLength(0)
    first.unmount()

    renderWithAuth(<MainPage />, { user: TEST_USERS.doctor })
    expect(await screen.findAllByRole('article')).toHaveLength(20)
    expect(screen.queryByRole('group', { name: 'Choose what the panel below shows' })).not.toBeInTheDocument()
    expect(screen.queryAllByRole('button', { name: /Acknowledge|Resolve/ })).toEqual([])
  })

  it.each([
    ['caregiver', 'Caregiver'],
    ['family', 'Family'],
  ] as const)('%s sees the family view with role badge "%s"', async (key, role) => {
    renderWithAuth(<MainPage />, { user: TEST_USERS[key] })
    expect(screen.getByRole('heading', { level: 1, name: 'My family' })).toBeInTheDocument()
    expect(within(screen.getByRole('button', { name: /user menu/i })).getByText(role)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^(Nurse|Doctor) dashboard$/ })).not.toBeInTheDocument()
    expect(await screen.findAllByRole('article')).toHaveLength(2)
    expect(screen.queryByText('Residents monitored')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Acknowledge|Resolve/ })).not.toBeInTheDocument()
  })

  it.each([
    [['nurse', 'doctor'], 'Nurse dashboard', 'Nurse'],
    [['doctor', 'nurse'], 'Nurse dashboard', 'Nurse'],
    [['family', 'doctor'], 'Doctor dashboard', 'Doctor'],
    [['caregiver', 'nurse'], 'Nurse dashboard', 'Nurse'],
    [['caregiver', 'family'], 'My family', 'Caregiver'],
  ])('a user in %j sees "%s" with role badge "%s"', async (groups, heading, role) => {
    renderWithAuth(<MainPage />, { user: withGroups(groups) })
    expect(screen.getByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
    expect(within(screen.getByRole('button', { name: /user menu/i })).getByText(role)).toBeInTheDocument()
    // Let the data load so nothing updates after the test ends.
    await screen.findAllByText(/Test Resident/)
  })

  it.each([
    ['caregiver', 'My family'],
    ['doctor', 'Doctor dashboard'],
    ['nurse', 'Nurse dashboard'],
  ] as const)('the demo banner is shown for a %s user in "%s" (criterion 20)', async (key, heading) => {
    renderWithAuth(<MainPage />, { user: TEST_USERS[key] })
    expect(screen.getByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
    const banner = screen.getByRole('note', { name: 'Demo data' })
    expect(banner).toHaveTextContent('Demo data')
    expect(banner).toHaveTextContent(BANNER)
    expect(banner.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    await screen.findAllByText(/Test Resident/)
  })
})

describe('no access', () => {
  it.each([[[]], [['admin']], [['Doctor']], [['Nurse']]])('a user in %j sees the no-access screen', async (groups) => {
    renderWithAuth(<MainPage />, { user: withGroups(groups) })
    expect(
      screen.getByRole('heading', { level: 1, name: "Your account isn't set up yet" }),
    ).toBeInTheDocument()
    expect(screen.getByText('Ask your care provider to assign you a role.')).toBeInTheDocument()
    expect(screen.queryByRole('note', { name: 'Demo data' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Test Resident/)).not.toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('SilverCare - No access'))
  })

  it('Sign out signs out and goes to /login', async () => {
    const user = userEvent.setup()
    const signOut = vi.fn(async () => {})
    renderWithAuth(<MainPage />, { user: TEST_USERS.noGroup, signOut })
    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByText('Login screen')).toBeInTheDocument()
    expect(signOut).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('location')).toHaveTextContent('/login')
  })

  it('still goes to /login, without an unhandled promise rejection, if sign-out fails', async () => {
    const user = userEvent.setup()
    const tracker = trackUnhandledRejections()
    const signOut = vi.fn(async () => {
      throw new Error('sign-out failed')
    })
    renderWithAuth(<MainPage />, { user: TEST_USERS.noGroup, signOut })
    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByText('Login screen')).toBeInTheDocument()
    await flushPromises()
    tracker.stop()
    expect(tracker.reasons).toEqual([])
  })

  it('has no axe violations', async () => {
    const { container } = renderWithAuth(<MainPage />, { user: TEST_USERS.noGroup })
    expect(await axeViolations(container)).toEqual([])
  })
})
