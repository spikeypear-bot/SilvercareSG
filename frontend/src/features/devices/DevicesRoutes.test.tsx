import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authMock, resetAuthMock } from '../../test/authMock'
import { resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS } from '../../test/fixtures'
import { deviceFleetLoader, resetDeviceFleetLoader } from '../../test/fleetMock'
import { renderApp } from '../../test/render'
import type { AuthUser } from '../../types/auth'
import type { DashboardView } from '../dashboard/useDashboardData'

vi.mock('../../lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/auth')>()
  const { authMock } = await import('../../test/authMock')
  return { ...actual, ...authMock }
})

vi.mock('../dashboard/useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../dashboard/useDashboardData')>()
  const { dashboardLoader } = await import('../../test/dashboardMock')
  const { deviceFleetLoader } = await import('../../test/fleetMock')
  return {
    ...actual,
    useDashboardData: (view: DashboardView) => actual.useDashboardData(view, dashboardLoader),
    useDeviceFleet: () => actual.useDeviceFleet(deviceFleetLoader),
  }
})

beforeEach(() => {
  resetAuthMock()
  resetDashboardLoader()
  resetDeviceFleetLoader()
})

const location = () => screen.getByTestId('location').textContent
const nav = () => screen.getByRole('navigation', { name: 'Staff area' })

describe('/devices route', () => {
  it('redirects to /login when signed out, without loading devices', async () => {
    renderApp('/devices')
    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
    expect(location()).toBe('/login')
    expect(deviceFleetLoader).not.toHaveBeenCalled()
  })

  it.each<[string, AuthUser]>([
    ['nurse', TEST_USERS.nurse],
    ['doctor', TEST_USERS.doctor],
    ['nurse + doctor', { ...TEST_USERS.nurse, groups: ['nurse', 'doctor'] }],
  ])('a %s user sees the Devices page', async (_, user) => {
    authMock.getCurrentUser.mockResolvedValue(user)
    renderApp('/devices')
    expect(await screen.findByRole('heading', { level: 1, name: 'Devices' })).toBeInTheDocument()
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(location()).toBe('/devices')
    expect(deviceFleetLoader).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(document.title).toBe('SilverCare - Devices'))
  })

  it.each([['caregiver', TEST_USERS.caregiver]] as const)('a %s user sees the staff-only notice and devices are never loaded', async (_, user) => {
    authMock.getCurrentUser.mockResolvedValue(user)
    renderApp('/devices')
    expect(await screen.findByRole('heading', { level: 2, name: 'This page is for care staff.' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Staff only' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(location()).toBe('/devices')
    expect(deviceFleetLoader).not.toHaveBeenCalled()
    await waitFor(() => expect(document.title).toBe('SilverCare - Staff only'))
  })

  it.each([
    ['no group', TEST_USERS.noGroup],
    ['only the removed "family" group', TEST_USERS.family],
  ] as const)('a user with %s sees the no-access page and devices are never loaded', async (_, user) => {
    authMock.getCurrentUser.mockResolvedValue(user)
    renderApp('/devices')
    expect(await screen.findByRole('heading', { level: 1, name: "Your account isn't set up yet" })).toBeInTheDocument()
    expect(screen.queryByText('This page is for care staff.')).not.toBeInTheDocument()
    expect(location()).toBe('/devices')
    expect(deviceFleetLoader).not.toHaveBeenCalled()
  })

  it('a caregiver on the main page (the family view) has no Staff area nav, and devices are not loaded', async () => {
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.caregiver)
    renderApp('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'My residents' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Staff area' })).not.toBeInTheDocument()
    expect(deviceFleetLoader).not.toHaveBeenCalled()
  })
})

describe('Staff area tabs between Dashboard and Devices', () => {
  it.each([
    ['nurse', TEST_USERS.nurse, 'Nurse dashboard'],
    ['doctor', TEST_USERS.doctor, 'Doctor dashboard'],
  ] as const)('a %s goes from the dashboard to Devices and back', async (_, account, dashboardTitle) => {
    const user = userEvent.setup()
    authMock.getCurrentUser.mockResolvedValue(account)
    renderApp('/')
    expect(await screen.findByRole('heading', { level: 1, name: dashboardTitle })).toBeInTheDocument()
    expect(within(nav()).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
    expect(deviceFleetLoader).not.toHaveBeenCalled()

    await user.click(within(nav()).getByRole('link', { name: 'Devices' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Devices' })).toBeInTheDocument()
    expect(location()).toBe('/devices')
    expect(within(nav()).getByRole('link', { name: 'Devices' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav()).getByRole('link', { name: 'Dashboard' })).not.toHaveAttribute('aria-current')
    expect(await screen.findByRole('table')).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('SilverCare - Devices'))

    await user.click(within(nav()).getByRole('link', { name: 'Dashboard' }))
    expect(await screen.findByRole('heading', { level: 1, name: dashboardTitle })).toBeInTheDocument()
    expect(location()).toBe('/')
    expect(within(nav()).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
    await waitFor(() => expect(document.title).toBe(`SilverCare - ${dashboardTitle}`))
  })

  it('a resident link on the Devices page opens the resident page, which has no Staff area nav', async () => {
    const user = userEvent.setup()
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.nurse)
    renderApp('/devices')
    const table = await screen.findByRole('table')
    const row = within(table).getAllByRole('row').find((r) => r.textContent?.includes('Test Resident C'))
    await user.click(within(row as HTMLElement).getByRole('link', { name: 'Test Resident C' }))
    expect(location()).toBe('/residents/res-0003')
    expect(await screen.findByRole('heading', { level: 1, name: 'Resident details' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Staff area' })).not.toBeInTheDocument()
  })
})
