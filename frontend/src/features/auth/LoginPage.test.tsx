import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthFlowError } from '../../lib/auth'
import { axeViolations } from '../../test/axe'
import { authMock, resetAuthMock } from '../../test/authMock'
import { TEST_USERS, VALID_PASSWORD, deferred } from '../../test/fixtures'
import { renderApp } from '../../test/render'
import type { AuthUser, SignInStep } from '../../types/auth'

vi.mock('../../lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/auth')>()
  const { authMock } = await import('../../test/authMock')
  return { ...actual, ...authMock }
})

const EMAIL = 'test.nurse.a@example.test'

beforeEach(() => {
  resetAuthMock()
})

/** After sign-in completes, the provider re-reads the session and finds `user`. */
function signInSucceedsAs(user: AuthUser) {
  authMock.signIn.mockImplementation(async () => {
    authMock.getCurrentUser.mockResolvedValue(user)
    return 'done'
  })
}

async function openLogin() {
  const user = userEvent.setup()
  const view = renderApp('/login')
  await screen.findByRole('heading', { name: 'Sign in to SilverCare' })
  return { user, ...view }
}

const emailInput = () => screen.getByLabelText('Email')
const passwordInput = () => screen.getByLabelText('Password')
const location = () => screen.getByTestId('location').textContent

async function submitPassword(user: ReturnType<typeof userEvent.setup>, email = EMAIL, password = 'Some-Pw-1!') {
  await user.type(emailInput(), email)
  await user.type(passwordInput(), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('login page layout', () => {
  it('shows the sign-in card, forgot-password link, and footer line', async () => {
    await openLogin()
    expect(emailInput()).toHaveAttribute('type', 'email')
    expect(passwordInput()).toHaveAttribute('type', 'password')
    expect(screen.getByRole('link', { name: 'Forgot password?' })).toHaveAttribute('href', '/forgot-password')
    expect(screen.getByRole('button', { name: 'Sign in' })).toHaveAttribute('type', 'submit')
    expect(screen.getByText('Accounts are created by your care provider.')).toBeInTheDocument()
    expect(screen.getByText('Peace of mind for every family')).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('SilverCare - Sign in'))
  })

  it('has no axe violations', async () => {
    const { container } = await openLogin()
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('valid credentials (criterion 10)', () => {
  it.each([
    ['doctor', 'Staff dashboard'],
    ['nurse', 'Staff dashboard'],
    ['caregiver', 'My family'],
    ['family', 'My family'],
    ['noGroup', "Your account isn't set up yet"],
  ] as const)('signs in a %s user and shows "%s" at /', async (key, heading) => {
    const account = TEST_USERS[key]
    signInSucceedsAs(account)
    const { user } = await openLogin()
    await submitPassword(user, account.email, VALID_PASSWORD)

    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
    expect(location()).toBe('/')
    expect(authMock.signIn).toHaveBeenCalledWith(account.email, VALID_PASSWORD)
  })

  it('shows a busy, disabled submit button while the request is in flight (criterion 17)', async () => {
    const pending = deferred<SignInStep>()
    authMock.signIn.mockReturnValue(pending.promise)
    const { user } = await openLogin()
    await submitPassword(user)

    const button = screen.getByRole('button', { name: 'Sign in' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')

    pending.reject(new AuthFlowError('incorrectCredentials'))
    await waitFor(() => expect(button).toBeEnabled())
    expect(button).not.toHaveAttribute('aria-busy')
  })
})

describe('wrong credentials (criterion 11)', () => {
  it.each([
    ['wrong password', EMAIL],
    ['unknown email', 'test.nobody@example.test'],
  ])('%s: shows "Incorrect email or password.", keeps the email, clears the password', async (_, email) => {
    authMock.signIn.mockRejectedValue(new AuthFlowError('incorrectCredentials'))
    const { user } = await openLogin()
    await submitPassword(user, email, 'Wrong-Pw-1!')

    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.')
    expect(emailInput()).toHaveValue(email)
    expect(passwordInput()).toHaveValue('')
    expect(passwordInput()).toHaveFocus()
    expect(location()).toBe('/login')
  })

  it('shows the generic message for network or unexpected errors', async () => {
    authMock.signIn.mockRejectedValue(new Error('network down'))
    const { user } = await openLogin()
    await submitPassword(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Please try again.')
    expect(screen.queryByText('network down')).not.toBeInTheDocument()
  })
})

describe('inline validation (criteria 16, 17)', () => {
  it('shows labelled errors linked with aria-describedby and does not call Cognito', async () => {
    const { user } = await openLogin()
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(emailInput()).toHaveAttribute('aria-invalid', 'true')
    expect(emailInput()).toHaveAccessibleDescription('Enter your email address.')
    expect(passwordInput()).toHaveAttribute('aria-invalid', 'true')
    expect(passwordInput()).toHaveAccessibleDescription('Enter your password.')
    expect(authMock.signIn).not.toHaveBeenCalled()
  })

  it('rejects a malformed email', async () => {
    const { user } = await openLogin()
    await submitPassword(user, 'not-an-email')
    expect(emailInput()).toHaveAccessibleDescription('Enter a valid email address.')
    expect(authMock.signIn).not.toHaveBeenCalled()
  })

  it('clears the errors once the input is valid', async () => {
    authMock.signIn.mockReturnValue(new Promise(() => {}))
    const { user } = await openLogin()
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await submitPassword(user)
    expect(emailInput()).not.toHaveAttribute('aria-invalid')
    expect(passwordInput()).not.toHaveAttribute('aria-invalid')
  })
})

describe('password show/hide toggle (criterion 17)', () => {
  it('has an accessible name and switches the input type', async () => {
    const { user } = await openLogin()
    await user.type(passwordInput(), 'secret-value')

    const toggle = screen.getByRole('button', { name: 'Show password' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(toggle)
    expect(passwordInput()).toHaveAttribute('type', 'text')
    const hide = screen.getByRole('button', { name: 'Hide password' })
    expect(hide).toHaveAttribute('aria-pressed', 'true')
    await user.click(hide)
    expect(passwordInput()).toHaveAttribute('type', 'password')
  })

  it('does not submit the form', async () => {
    const { user } = await openLogin()
    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(authMock.signIn).not.toHaveBeenCalled()
  })
})

describe('temporary password: new password required (criterion 12)', () => {
  async function reachNewPasswordStep() {
    authMock.signIn.mockResolvedValue('newPasswordRequired')
    const ctx = await openLogin()
    await submitPassword(ctx.user, EMAIL, 'Temp-Pw-123!')
    await screen.findByRole('heading', { name: 'Choose a new password' })
    return ctx
  }

  const newPw = () => screen.getByLabelText('New password')
  const confirmPw = () => screen.getByLabelText('Confirm new password')
  const rule = (label: string) => screen.getByText(label).closest('li')

  it('asks for a new password and confirmation, with a live rules checklist', async () => {
    const { user } = await reachNewPasswordStep()
    expect(newPw()).toHaveFocus()
    expect(confirmPw()).toBeInTheDocument()

    const labels = [
      'At least 12 characters',
      'A lowercase letter',
      'An uppercase letter',
      'A number',
      'A symbol, such as ! @ # $',
    ]
    labels.forEach((label) => {
      expect(rule(label)).toHaveAttribute('data-met', 'false')
      expect(rule(label)).toHaveTextContent('(not yet)')
    })

    await user.type(newPw(), 'abc')
    expect(rule('A lowercase letter')).toHaveAttribute('data-met', 'true')
    expect(rule('A lowercase letter')).toHaveTextContent('(done)')
    expect(rule('An uppercase letter')).toHaveAttribute('data-met', 'false')

    await user.type(newPw(), 'DEF12!xyzw')
    labels.forEach((label) => expect(rule(label)).toHaveAttribute('data-met', 'true'))
  })

  it('links the checklist to the new password field', async () => {
    await reachNewPasswordStep()
    const describedBy = newPw().getAttribute('aria-describedby') ?? ''
    const checklist = screen.getByText('Your password needs:').parentElement
    expect(checklist?.id).toBeTruthy()
    expect(describedBy.split(' ')).toContain(checklist?.id)
  })

  it('rejects a password that breaks the rules', async () => {
    const { user } = await reachNewPasswordStep()
    await user.type(newPw(), 'short')
    await user.type(confirmPw(), 'short')
    await user.click(screen.getByRole('button', { name: 'Save password and sign in' }))
    expect(newPw()).toHaveAttribute('aria-invalid', 'true')
    expect(newPw()).toHaveAccessibleDescription(expect.stringContaining("Your new password doesn't meet all the rules."))
    expect(authMock.completeNewPassword).not.toHaveBeenCalled()
  })

  it('rejects a confirmation that does not match', async () => {
    const { user } = await reachNewPasswordStep()
    await user.type(newPw(), VALID_PASSWORD)
    await user.type(confirmPw(), `${VALID_PASSWORD}x`)
    await user.click(screen.getByRole('button', { name: 'Save password and sign in' }))
    expect(confirmPw()).toHaveAccessibleDescription("The passwords don't match.")
    expect(authMock.completeNewPassword).not.toHaveBeenCalled()
  })

  it('signs the user in on success', async () => {
    const { user } = await reachNewPasswordStep()
    authMock.completeNewPassword.mockImplementation(async () => {
      authMock.getCurrentUser.mockResolvedValue(TEST_USERS.nurse)
      return 'done'
    })
    await user.type(newPw(), VALID_PASSWORD)
    await user.type(confirmPw(), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Save password and sign in' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Staff dashboard' })).toBeInTheDocument()
    expect(authMock.completeNewPassword).toHaveBeenCalledWith(VALID_PASSWORD)
  })

  it('busy state while saving', async () => {
    const { user } = await reachNewPasswordStep()
    authMock.completeNewPassword.mockReturnValue(new Promise(() => {}))
    await user.type(newPw(), VALID_PASSWORD)
    await user.type(confirmPw(), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Save password and sign in' }))
    expect(screen.getByRole('button', { name: 'Save password and sign in' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save password and sign in' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('button', { name: 'Back to sign in' })).toBeDisabled()
  })

  it('moves on to the code step when the user also has TOTP', async () => {
    const { user } = await reachNewPasswordStep()
    authMock.completeNewPassword.mockResolvedValue('totpCode')
    await user.type(newPw(), VALID_PASSWORD)
    await user.type(confirmPw(), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Save password and sign in' }))
    expect(await screen.findByRole('heading', { name: 'Enter your code' })).toBeInTheDocument()
  })

  it('returns to the password step with a notice when the session expired', async () => {
    const { user } = await reachNewPasswordStep()
    authMock.completeNewPassword.mockRejectedValue(new AuthFlowError('sessionExpired'))
    await user.type(newPw(), VALID_PASSWORD)
    await user.type(confirmPw(), VALID_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Save password and sign in' }))
    expect(await screen.findByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Your sign-in session has expired. Please sign in again.',
    )
    expect(emailInput()).toHaveValue(EMAIL)
    expect(passwordInput()).toHaveValue('')
  })

  it('has no axe violations', async () => {
    const { container } = await reachNewPasswordStep()
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('authenticator app code (criterion 13)', () => {
  async function reachCodeStep() {
    authMock.signIn.mockResolvedValue('totpCode')
    const ctx = await openLogin()
    await submitPassword(ctx.user, EMAIL, VALID_PASSWORD)
    await screen.findByRole('heading', { name: 'Enter your code' })
    return ctx
  }

  const codeInput = () => screen.getByLabelText('6-digit code')

  it('asks for the 6-digit code after the password', async () => {
    await reachCodeStep()
    expect(codeInput()).toHaveFocus()
    expect(codeInput()).toHaveAttribute('autocomplete', 'one-time-code')
    expect(codeInput()).toHaveAttribute('inputmode', 'numeric')
    expect(codeInput()).toHaveAttribute('maxlength', '6')
  })

  it('a wrong code shows "That code didn\'t work. Try again." and stays on the code step', async () => {
    const { user } = await reachCodeStep()
    authMock.confirmTotpCode.mockRejectedValue(new AuthFlowError('codeMismatch'))
    await user.type(codeInput(), '000000')
    await user.click(screen.getByRole('button', { name: 'Verify' }))

    await waitFor(() => expect(codeInput()).toHaveAccessibleDescription("That code didn't work. Try again."))
    expect(screen.getByRole('heading', { name: 'Enter your code' })).toBeInTheDocument()
    expect(codeInput()).toHaveAttribute('aria-invalid', 'true')
    expect(codeInput()).toHaveValue('')
    expect(location()).toBe('/login')
  })

  it('a correct code signs the user in', async () => {
    const { user } = await reachCodeStep()
    authMock.confirmTotpCode.mockImplementation(async () => {
      authMock.getCurrentUser.mockResolvedValue(TEST_USERS.family)
      return 'done'
    })
    await user.type(codeInput(), '123456')
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'My family' })).toBeInTheDocument()
    expect(authMock.confirmTotpCode).toHaveBeenCalledWith('123456')
  })

  it('only accepts digits and requires 6 of them', async () => {
    const { user } = await reachCodeStep()
    await user.type(codeInput(), '12ab3')
    expect(codeInput()).toHaveValue('123')
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(codeInput()).toHaveAccessibleDescription('Enter the 6-digit code from your app.')
    expect(authMock.confirmTotpCode).not.toHaveBeenCalled()
  })

  it('busy state while verifying', async () => {
    const { user } = await reachCodeStep()
    authMock.confirmTotpCode.mockReturnValue(new Promise(() => {}))
    await user.type(codeInput(), '123456')
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(screen.getByRole('button', { name: 'Verify' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Verify' })).toHaveAttribute('aria-busy', 'true')
  })

  it('"Back to sign in" returns to the password step and keeps the email', async () => {
    const { user } = await reachCodeStep()
    await user.click(screen.getByRole('button', { name: 'Back to sign in' }))
    expect(screen.getByRole('heading', { name: 'Sign in to SilverCare' })).toBeInTheDocument()
    expect(emailInput()).toHaveValue(EMAIL)
    expect(passwordInput()).toHaveValue('')
    expect(passwordInput()).toHaveFocus()
  })

  it('has no axe violations', async () => {
    const { container } = await reachCodeStep()
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('privacy', () => {
  it('does not log or put credentials in the title, URL, or storage', async () => {
    const log = vi.spyOn(console, 'log')
    authMock.signIn.mockResolvedValue('totpCode')
    const { user } = await openLogin()
    await submitPassword(user, EMAIL, VALID_PASSWORD)
    await screen.findByRole('heading', { name: 'Enter your code' })

    expect(log).not.toHaveBeenCalled()
    expect(document.title).not.toContain(EMAIL)
    expect(location()).toBe('/login')
    const stored = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage })
    expect(stored).not.toContain(EMAIL)
    expect(stored).not.toContain(VALID_PASSWORD)
    log.mockRestore()
  })
})
