import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthFlowError } from './lib/auth'
import { authMock, resetAuthMock } from './test/authMock'
import { TEST_USERS, deferred } from './test/fixtures'
import { renderApp } from './test/render'
import { flushPromises, trackUnhandledRejections } from './test/unhandled'
import type { AuthUser } from './types/auth'

vi.mock('./lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./lib/auth')>()
  const { authMock } = await import('./test/authMock')
  return { ...actual, ...authMock }
})

beforeEach(() => {
  resetAuthMock()
})

const location = () => screen.getByTestId('location').textContent

describe('signed out (criterion 9)', () => {
  it.each(['/', '/account', '/residents/res-0001', '/some/unknown/route'])(
    'redirects %s to /login',
    async (path) => {
      renderApp(path)
      expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
      expect(location()).toBe('/login')
    },
  )

  it.each([
    ['/login', 'Sign in to SilverCare'],
    ['/forgot-password', 'Reset your password'],
  ])('lets them open %s', async (path, heading) => {
    renderApp(path)
    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
    expect(location()).toBe(path)
  })

  it('shows a loading status while the session is being checked', async () => {
    const session = deferred<AuthUser | null>()
    authMock.getCurrentUser.mockReturnValue(session.promise)
    renderApp('/')
    expect(screen.getByRole('status')).toHaveTextContent('Loading')
    session.resolve(null)
    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
  })

  it('treats a failing session check as signed out', async () => {
    authMock.getCurrentUser.mockRejectedValue(new AuthFlowError('generic'))
    renderApp('/account')
    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
  })
})

describe('signed in', () => {
  it.each(['/login', '/forgot-password'])('redirects %s to /', async (path) => {
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.nurse)
    renderApp(path)
    expect(await screen.findByRole('heading', { name: 'Staff dashboard' })).toBeInTheDocument()
    expect(location()).toBe('/')
  })

  it('redirects unknown routes to /', async () => {
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.family)
    renderApp('/nothing-here')
    expect(await screen.findByRole('heading', { name: 'My family' })).toBeInTheDocument()
    expect(location()).toBe('/')
  })

  it.each(Object.entries(TEST_USERS))('/account is open to %s users (criterion 15)', async (_, user) => {
    authMock.getCurrentUser.mockResolvedValue(user)
    renderApp('/account')
    expect(await screen.findByRole('heading', { name: 'Account settings' })).toBeInTheDocument()
    expect(location()).toBe('/account')
  })
})

describe('sign out from the header user menu (criterion 16)', () => {
  it('calls signOut and returns to /login', async () => {
    const user = userEvent.setup()
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.doctor)
    renderApp('/')

    await user.click(await screen.findByRole('button', { name: /user menu/i }))
    await user.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
    expect(authMock.signOut).toHaveBeenCalledTimes(1)
    expect(location()).toBe('/login')
  })

  it('shows the sign-out button as busy while signing out', async () => {
    const user = userEvent.setup()
    const pending = deferred<void>()
    authMock.signOut.mockReturnValue(pending.promise)
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.doctor)
    renderApp('/')

    await user.click(await screen.findByRole('button', { name: /user menu/i }))
    const signOutButton = screen.getByRole('button', { name: 'Sign out' })
    await user.click(signOutButton)
    expect(signOutButton).toBeDisabled()
    expect(signOutButton).toHaveAttribute('aria-busy', 'true')

    pending.resolve()
    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
  })

  it('still returns to /login, without an unhandled promise rejection, if Amplify signOut fails', async () => {
    const user = userEvent.setup()
    const tracker = trackUnhandledRejections()
    authMock.signOut.mockRejectedValue(new AuthFlowError('generic'))
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.doctor)
    renderApp('/')

    await user.click(await screen.findByRole('button', { name: /user menu/i }))
    await user.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
    await flushPromises()
    tracker.stop()
    expect(tracker.reasons).toEqual([])
  })

  it('keeps menu items hidden until the menu is opened, and closes on Escape', async () => {
    const user = userEvent.setup()
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.caregiver)
    renderApp('/')

    const menuButton = await screen.findByRole('button', { name: /user menu/i })
    expect(menuButton).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('link', { name: 'Account settings' })).not.toBeInTheDocument()
    expect(within(menuButton).getByText('Test Caregiver A')).toBeInTheDocument()
    expect(within(menuButton).getByText('Caregiver')).toBeInTheDocument()

    await user.click(menuButton)
    expect(menuButton).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: 'Account settings' })).toHaveAttribute('href', '/account')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(menuButton).toHaveAttribute('aria-expanded', 'false'))
    expect(menuButton).toHaveFocus()
  })

  it('Account settings link opens /account', async () => {
    const user = userEvent.setup()
    authMock.getCurrentUser.mockResolvedValue(TEST_USERS.nurse)
    renderApp('/')
    await user.click(await screen.findByRole('button', { name: /user menu/i }))
    await user.click(screen.getByRole('link', { name: 'Account settings' }))
    expect(await screen.findByRole('heading', { name: 'Account settings' })).toBeInTheDocument()
    expect(location()).toBe('/account')
  })
})
