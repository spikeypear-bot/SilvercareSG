import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AuthFlowError } from '../../lib/auth'
import { axeViolations } from '../../test/axe'
import { authMock, resetAuthMock } from '../../test/authMock'
import {
  OTHER_VALID_PASSWORD,
  TEST_TOTP_SECRET,
  TEST_TOTP_URI,
  TEST_USERS,
  VALID_PASSWORD,
  deferred,
} from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { AuthUser } from '../../types/auth'
import { AccountPage } from './AccountPage'

vi.mock('../../lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/auth')>()
  const { authMock } = await import('../../test/authMock')
  return { ...actual, ...authMock }
})

beforeEach(() => {
  resetAuthMock()
  authMock.changePassword.mockResolvedValue(undefined)
  authMock.startTotpSetup.mockResolvedValue({ secret: TEST_TOTP_SECRET, uri: TEST_TOTP_URI })
  authMock.finishTotpSetup.mockResolvedValue(undefined)
  authMock.disableTotp.mockResolvedValue(undefined)
})

function renderAccount(user: AuthUser = TEST_USERS.nurse) {
  const events = userEvent.setup()
  const view = renderWithAuth(<AccountPage />, { user, path: '/account' })
  return { user: events, ...view }
}

const card = (title: string) =>
  screen.getByRole('heading', { level: 2, name: title }).closest('section') as HTMLElement

describe('profile', () => {
  it.each([
    ['doctor', 'Doctor'],
    ['nurse', 'Nurse'],
    ['caregiver', 'Caregiver'],
    ['family', 'Family'],
    ['noGroup', 'No role'],
  ] as const)('shows name, email, and role for a %s user', async (key, role) => {
    const account = TEST_USERS[key]
    renderAccount(account)
    const profile = within(card('Profile'))
    expect(profile.getByText('Name').nextElementSibling).toHaveTextContent(account.name)
    expect(profile.getByText('Email').nextElementSibling).toHaveTextContent(account.email)
    expect(profile.getByText('Role').nextElementSibling).toHaveTextContent(role)
    await screen.findByText('Off')
  })

  it('has the page title without personal data', async () => {
    renderAccount()
    await waitFor(() => expect(document.title).toBe('SilverCare - Account settings'))
    await screen.findByText('Off')
  })
})

describe('change password', () => {
  const current = () => within(card('Change password')).getByLabelText('Current password')
  const next = () => within(card('Change password')).getByLabelText('New password')
  const submit = () => within(card('Change password')).getByRole('button', { name: 'Change password' })

  it('changes the password with current + new and shows a success notice', async () => {
    const { user } = renderAccount()
    await user.type(current(), VALID_PASSWORD)
    await user.type(next(), OTHER_VALID_PASSWORD)
    await user.click(submit())

    expect(await within(card('Change password')).findByRole('status')).toHaveTextContent(
      'Your password has been changed.',
    )
    expect(authMock.changePassword).toHaveBeenCalledWith(VALID_PASSWORD, OTHER_VALID_PASSWORD)
    expect(current()).toHaveValue('')
    expect(next()).toHaveValue('')
  })

  it('validates inline with labelled errors', async () => {
    const { user } = renderAccount()
    await user.type(next(), 'weak')
    await user.click(submit())
    expect(current()).toHaveAccessibleDescription('Enter your current password.')
    expect(next()).toHaveAccessibleDescription(
      expect.stringContaining("Your new password doesn't meet all the rules."),
    )
    expect(authMock.changePassword).not.toHaveBeenCalled()
  })

  it('shows the rules checklist, updating as the user types', async () => {
    const { user } = renderAccount()
    const rule = (label: string) =>
      within(card('Change password')).getByText(label).closest('li')
    expect(rule('A number')).toHaveAttribute('data-met', 'false')
    await user.type(next(), '7')
    expect(rule('A number')).toHaveAttribute('data-met', 'true')
  })

  it('shows "Your current password is incorrect." on the current field', async () => {
    authMock.changePassword.mockRejectedValue(new AuthFlowError('incorrectCurrentPassword'))
    const { user } = renderAccount()
    await user.type(current(), 'Wrong-Pw-123!')
    await user.type(next(), OTHER_VALID_PASSWORD)
    await user.click(submit())
    await waitFor(() =>
      expect(current()).toHaveAccessibleDescription('Your current password is incorrect.'),
    )
    expect(current()).toHaveValue('')
  })

  it('shows the generic error for unexpected failures', async () => {
    authMock.changePassword.mockRejectedValue(new Error('boom'))
    const { user } = renderAccount()
    await user.type(current(), VALID_PASSWORD)
    await user.type(next(), OTHER_VALID_PASSWORD)
    await user.click(submit())
    expect(await within(card('Change password')).findByRole('alert')).toHaveTextContent(
      'Something went wrong. Please try again.',
    )
  })

  it('shows a busy, disabled button while saving', async () => {
    authMock.changePassword.mockReturnValue(new Promise(() => {}))
    const { user } = renderAccount()
    await user.type(current(), VALID_PASSWORD)
    await user.type(next(), OTHER_VALID_PASSWORD)
    await user.click(submit())
    expect(submit()).toBeDisabled()
    expect(submit()).toHaveAttribute('aria-busy', 'true')
  })

  it('both password fields have show/hide toggles with accessible names', () => {
    renderAccount()
    const section = within(card('Change password'))
    expect(section.getByRole('button', { name: 'Show current password' })).toBeInTheDocument()
    expect(section.getByRole('button', { name: 'Show new password' })).toBeInTheDocument()
  })
})

describe('two-step verification', () => {
  it('shows a loading status, then Off with a set-up button', async () => {
    const status = deferred<boolean>()
    authMock.isTotpEnabled.mockReturnValue(status.promise)
    renderAccount()
    expect(twoStep().getByRole('status')).toHaveTextContent('Loading two-step verification status')
    status.resolve(false)
    expect(await twoStep().findByText('Off')).toBeInTheDocument()
    expect(twoStep().getByRole('button', { name: 'Set up authenticator app' })).toBeInTheDocument()
    expect(twoStep().queryByRole('button', { name: 'Turn off' })).not.toBeInTheDocument()
  })

  it('shows an error with Try again when the status cannot be loaded', async () => {
    authMock.isTotpEnabled.mockRejectedValueOnce(new AuthFlowError('generic'))
    const { user } = renderAccount()
    expect(await twoStep().findByRole('alert')).toHaveTextContent(
      "We couldn't load your two-step verification status.",
    )
    authMock.isTotpEnabled.mockResolvedValue(true)
    await user.click(twoStep().getByRole('button', { name: 'Try again' }))
    expect(await twoStep().findByText('On')).toBeInTheDocument()
    expect(authMock.isTotpEnabled).toHaveBeenCalledTimes(2)
  })

  async function startSetup(account: AuthUser = TEST_USERS.nurse) {
    const ctx = renderAccount(account)
    await ctx.user.click(await twoStep().findByRole('button', { name: 'Set up authenticator app' }))
    await twoStep().findByTestId('totp-secret')
    return ctx
  }

  it('shows a locally generated QR code and the secret as text', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const { container } = await startSetup()

    expect(authMock.startTotpSetup).toHaveBeenCalledWith(TEST_USERS.nurse.email)
    const qr = twoStep().getByRole('img', { name: 'QR code for setting up your authenticator app' })
    expect(qr.tagName.toLowerCase()).toBe('svg')
    expect(qr.querySelectorAll('path').length).toBeGreaterThan(0)
    expect(container.querySelector('img[src^="http"]')).toBeNull()
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(twoStep().getByTestId('totp-secret')).toHaveTextContent(TEST_TOTP_SECRET)
    expect(twoStep().getByLabelText('6-digit code')).toHaveFocus()
    fetchSpy.mockRestore()
  })

  it('does not store the secret in browser storage', async () => {
    await startSetup()
    const stored = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage })
    expect(stored).not.toContain(TEST_TOTP_SECRET)
  })

  it('verifies a code, turns TOTP on, and hides the secret', async () => {
    const { user } = await startSetup()
    await user.type(twoStep().getByLabelText('6-digit code'), '123456')
    await user.click(twoStep().getByRole('button', { name: 'Verify and turn on' }))

    expect(await twoStep().findByText('On')).toBeInTheDocument()
    expect(authMock.finishTotpSetup).toHaveBeenCalledWith('123456')
    expect(twoStep().getByRole('status')).toHaveTextContent('Two-step verification is on.')
    expect(screen.queryByText(TEST_TOTP_SECRET)).not.toBeInTheDocument()
    expect(twoStep().getByRole('button', { name: 'Turn off' })).toBeInTheDocument()
  })

  it('a wrong code shows an inline error and keeps the setup open', async () => {
    authMock.finishTotpSetup.mockRejectedValue(new AuthFlowError('codeMismatch'))
    const { user } = await startSetup()
    await user.type(twoStep().getByLabelText('6-digit code'), '000000')
    await user.click(twoStep().getByRole('button', { name: 'Verify and turn on' }))
    await waitFor(() =>
      expect(twoStep().getByLabelText('6-digit code')).toHaveAccessibleDescription(
        "That code didn't work. Try again.",
      ),
    )
    expect(twoStep().getByTestId('totp-secret')).toBeInTheDocument()
    expect(twoStep().getByText('Off')).toBeInTheDocument()
  })

  it('requires a 6-digit code before calling Cognito', async () => {
    const { user } = await startSetup()
    await user.type(twoStep().getByLabelText('6-digit code'), '12')
    await user.click(twoStep().getByRole('button', { name: 'Verify and turn on' }))
    expect(twoStep().getByLabelText('6-digit code')).toHaveAccessibleDescription(
      'Enter the 6-digit code from your app.',
    )
    expect(authMock.finishTotpSetup).not.toHaveBeenCalled()
  })

  it('busy state while verifying', async () => {
    authMock.finishTotpSetup.mockReturnValue(new Promise(() => {}))
    const { user } = await startSetup()
    await user.type(twoStep().getByLabelText('6-digit code'), '123456')
    await user.click(twoStep().getByRole('button', { name: 'Verify and turn on' }))
    expect(twoStep().getByRole('button', { name: 'Verify and turn on' })).toBeDisabled()
    expect(twoStep().getByRole('button', { name: 'Verify and turn on' })).toHaveAttribute('aria-busy', 'true')
    expect(twoStep().getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })

  it('Cancel closes the setup and forgets the secret', async () => {
    const { user } = await startSetup()
    await user.click(twoStep().getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText(TEST_TOTP_SECRET)).not.toBeInTheDocument()
    expect(twoStep().getByRole('button', { name: 'Set up authenticator app' })).toBeInTheDocument()
  })

  it('shows the generic error if setup cannot start', async () => {
    authMock.startTotpSetup.mockRejectedValue(new AuthFlowError('generic'))
    const { user } = renderAccount()
    await user.click(await twoStep().findByRole('button', { name: 'Set up authenticator app' }))
    expect(await twoStep().findByRole('alert')).toHaveTextContent('Something went wrong. Please try again.')
  })

  it('turns TOTP off when it is on', async () => {
    authMock.isTotpEnabled.mockResolvedValue(true)
    const { user } = renderAccount()
    expect(await twoStep().findByText('On')).toBeInTheDocument()
    await user.click(twoStep().getByRole('button', { name: 'Turn off' }))
    expect(await twoStep().findByText('Off')).toBeInTheDocument()
    expect(authMock.disableTotp).toHaveBeenCalledTimes(1)
    expect(twoStep().getByRole('status')).toHaveTextContent('Two-step verification is off.')
  })

  it('status On/Off is shown as text with an icon, not colour alone', async () => {
    authMock.isTotpEnabled.mockResolvedValue(true)
    renderAccount()
    const on = await twoStep().findByText('On')
    expect(on.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })
})

describe('accessibility', () => {
  it('has no axe violations (status loaded)', async () => {
    const { container } = renderAccount()
    await screen.findByText('Off')
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations (TOTP setup open)', async () => {
    const { user, container } = renderAccount()
    await user.click(await twoStep().findByRole('button', { name: 'Set up authenticator app' }))
    await twoStep().findByTestId('totp-secret')
    expect(await axeViolations(container)).toEqual([])
  })

  it('headings are in order: h1 page title, h2 cards', async () => {
    renderAccount()
    await screen.findByText('Off')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Account settings')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([
      'Profile',
      'Change password',
      'Two-step verification',
    ])
  })
})

function twoStep() {
  return within(card('Two-step verification'))
}
