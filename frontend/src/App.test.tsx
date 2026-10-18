import { render, screen } from '@testing-library/react'
import App from './App'
import { authMock, resetAuthMock } from './test/authMock'

vi.mock('./lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./lib/auth')>()
  const { authMock } = await import('./test/authMock')
  return { ...actual, ...authMock }
})

beforeEach(() => {
  resetAuthMock()
  window.history.replaceState(null, '', '/')
})

it('sends a signed-out visitor at / to the sign-in page', async () => {
  render(<App />)
  expect(
    await screen.findByRole('heading', { level: 1, name: 'Sign in to SilverCare' }),
  ).toBeInTheDocument()
  expect(window.location.pathname).toBe('/login')
  expect(authMock.getCurrentUser).toHaveBeenCalled()
})
