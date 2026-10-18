import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthFlowError } from '../../lib/auth'
import { axeViolations } from '../../test/axe'
import { authMock, resetAuthMock } from '../../test/authMock'
import { VALID_PASSWORD, deferred } from '../../test/fixtures'
import { renderApp } from '../../test/render'

vi.mock('../../lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/auth')>()
  const { authMock } = await import('../../test/authMock')
  return { ...actual, ...authMock }
})

const EMAIL = 'test.family.a@example.test'
const NEUTRAL = "If an account exists for that email, we've sent a code."

beforeEach(() => {
  resetAuthMock()
  authMock.requestPasswordReset.mockResolvedValue(undefined)
  authMock.confirmPasswordReset.mockResolvedValue(undefined)
})

async function openPage() {
  const user = userEvent.setup()
  const view = renderApp('/forgot-password')
  await screen.findByRole('heading', { name: 'Reset your password' })
  return { user, ...view }
}

async function reachResetStep() {
  const ctx = await openPage()
  await ctx.user.type(screen.getByLabelText('Email'), EMAIL)
  await ctx.user.click(screen.getByRole('button', { name: 'Send code' }))
  await screen.findByRole('heading', { name: 'Choose a new password' })
  return ctx
}

const codeInput = () => screen.getByLabelText('Code from the email')
const newPw = () => screen.getByLabelText('New password')
const location = () => screen.getByTestId('location').textContent

describe('step 1: email', () => {
  it('shows the email field, Send code, and Back to sign in', async () => {
    const { user } = await openPage()
    await waitFor(() => expect(document.title).toBe('SilverCare - Forgot password'))
    await user.click(screen.getByRole('link', { name: 'Back to sign in' }))
    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
  })

  it('validates the email inline', async () => {
    const { user } = await openPage()
    await user.click(screen.getByRole('button', { name: 'Send code' }))
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('Enter your email address.')
    await user.type(screen.getByLabelText('Email'), 'nope')
    await user.click(screen.getByRole('button', { name: 'Send code' }))
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('Enter a valid email address.')
    expect(authMock.requestPasswordReset).not.toHaveBeenCalled()
  })

  it('sends a code and shows the neutral message (criterion 14)', async () => {
    await reachResetStep()
    expect(authMock.requestPasswordReset).toHaveBeenCalledWith(EMAIL)
    expect(screen.getByRole('status')).toHaveTextContent(NEUTRAL)
    expect(codeInput()).toHaveFocus()
  })

  it('shows a busy, disabled Send code button while the request is in flight', async () => {
    const pending = deferred<void>()
    authMock.requestPasswordReset.mockReturnValue(pending.promise)
    const { user } = await openPage()
    await user.type(screen.getByLabelText('Email'), EMAIL)
    await user.click(screen.getByRole('button', { name: 'Send code' }))
    const button = screen.getByRole('button', { name: 'Send code' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    pending.resolve()
    await screen.findByRole('heading', { name: 'Choose a new password' })
  })

  it('shows the generic error on network failure and stays on the email step', async () => {
    authMock.requestPasswordReset.mockRejectedValue(new AuthFlowError('generic'))
    const { user } = await openPage()
    await user.type(screen.getByLabelText('Email'), EMAIL)
    await user.click(screen.getByRole('button', { name: 'Send code' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Please try again.')
    expect(screen.getByRole('heading', { name: 'Reset your password' })).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveValue(EMAIL)
  })

  it('has no axe violations', async () => {
    const { container } = await openPage()
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('step 2: code and new password', () => {
  it('shows the rules checklist, updating as the user types', async () => {
    const { user } = await reachResetStep()
    const rule = (label: string) => screen.getByText(label).closest('li')
    expect(rule('At least 12 characters')).toHaveAttribute('data-met', 'false')
    await user.type(newPw(), 'abcdefghijkl')
    expect(rule('At least 12 characters')).toHaveAttribute('data-met', 'true')
    expect(rule('An uppercase letter')).toHaveAttribute('data-met', 'false')
    expect(newPw()).toHaveAccessibleDescription(expect.stringContaining('Your password needs:'))
  })

  it('resets the password and sends the user to /login with a success notice', async () => {
    const { user } = await reachResetStep()
    await user.type(codeInput(), '123456')
    await user.type(newPw(), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Reset password' }))

    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
    expect(location()).toBe('/login')
    expect(screen.getByRole('status')).toHaveTextContent(
      'Your password has been reset. Sign in with your new password.',
    )
    expect(authMock.confirmPasswordReset).toHaveBeenCalledWith(EMAIL, '123456', VALID_PASSWORD)
  })

  it('validates code and password rules inline', async () => {
    const { user } = await reachResetStep()
    await user.type(newPw(), 'weak')
    await user.click(screen.getByRole('button', { name: 'Reset password' }))
    expect(codeInput()).toHaveAccessibleDescription('Enter the code from the email.')
    expect(newPw()).toHaveAccessibleDescription(
      expect.stringContaining("Your new password doesn't meet all the rules."),
    )
    expect(authMock.confirmPasswordReset).not.toHaveBeenCalled()
  })

  it('shows "That code is invalid or has expired." on the code field for a bad code', async () => {
    authMock.confirmPasswordReset.mockRejectedValue(new AuthFlowError('codeInvalidOrExpired'))
    const { user } = await reachResetStep()
    await user.type(codeInput(), '000000')
    await user.type(newPw(), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Reset password' }))

    await waitFor(() =>
      expect(codeInput()).toHaveAccessibleDescription('That code is invalid or has expired.'),
    )
    expect(screen.getByRole('heading', { name: 'Choose a new password' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset password' })).toBeEnabled()
  })

  it('shows the generic error for anything else', async () => {
    authMock.confirmPasswordReset.mockRejectedValue(new Error('boom'))
    const { user } = await reachResetStep()
    await user.type(codeInput(), '123456')
    await user.type(newPw(), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Reset password' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Please try again.')
  })

  it('busy state while resetting', async () => {
    authMock.confirmPasswordReset.mockReturnValue(new Promise(() => {}))
    const { user } = await reachResetStep()
    await user.type(codeInput(), '123456')
    await user.type(newPw(), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Reset password' }))
    expect(screen.getByRole('button', { name: 'Reset password' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reset password' })).toHaveAttribute('aria-busy', 'true')
  })

  it('has a Back to sign in link and a show/hide toggle', async () => {
    await reachResetStep()
    expect(screen.getByRole('link', { name: 'Back to sign in' })).toHaveAttribute('href', '/login')
    expect(screen.getByRole('button', { name: 'Show new password' })).toBeInTheDocument()
  })

  it('does not put the email in the URL', async () => {
    await reachResetStep()
    expect(location()).toBe('/forgot-password')
    expect(window.location.search).toBe('')
  })

  it('has no axe violations', async () => {
    const { container } = await reachResetStep()
    expect(await axeViolations(container)).toEqual([])
  })
})
