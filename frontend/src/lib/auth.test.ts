/**
 * Unit tests for the Amplify wrapper itself. This is the one file that mocks
 * `aws-amplify` directly; every other test mocks `src/lib/auth.ts`.
 */
/** Reads a file from disk without pulling Node types into the app's tsconfig. */
async function readTextFile(path: string): Promise<string> {
  const fsModule = 'node:fs/promises'
  const fs = (await import(/* @vite-ignore */ fsModule)) as {
    readFile: (path: string, encoding: 'utf8') => Promise<string>
  }
  return fs.readFile(path, 'utf8')
}

const amplify = vi.hoisted(() => ({
  configure: vi.fn(),
  fetchAuthSession: vi.fn(),
  fetchUserAttributes: vi.fn(),
  fetchMFAPreference: vi.fn(),
  signIn: vi.fn(),
  confirmSignIn: vi.fn(),
  signOut: vi.fn(),
  resetPassword: vi.fn(),
  confirmResetPassword: vi.fn(),
  updatePassword: vi.fn(),
  setUpTOTP: vi.fn(),
  verifyTOTPSetup: vi.fn(),
  updateMFAPreference: vi.fn(),
}))

vi.mock('aws-amplify', () => ({ Amplify: { configure: amplify.configure } }))
vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: amplify.fetchAuthSession,
  fetchUserAttributes: amplify.fetchUserAttributes,
  fetchMFAPreference: amplify.fetchMFAPreference,
  signIn: amplify.signIn,
  confirmSignIn: amplify.confirmSignIn,
  signOut: amplify.signOut,
  resetPassword: amplify.resetPassword,
  confirmResetPassword: amplify.confirmResetPassword,
  updatePassword: amplify.updatePassword,
  setUpTOTP: amplify.setUpTOTP,
  verifyTOTPSetup: amplify.verifyTOTPSetup,
  updateMFAPreference: amplify.updateMFAPreference,
}))

import * as auth from './auth'

/** A Cognito-style error whose message contains data that must never reach the UI. */
function cognitoError(name: string): Error {
  const error = new Error('Detail for test.user@example.test, code 123456')
  error.name = name
  return error
}

const step = (signInStep: string) => ({ isSignedIn: signInStep === 'DONE', nextStep: { signInStep } })

beforeEach(() => {
  Object.values(amplify).forEach((fn) => fn.mockReset())
})

describe('configuration (criterion 8)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('configures Amplify once from VITE_USER_POOL_ID and VITE_USER_POOL_CLIENT_ID', async () => {
    vi.stubEnv('VITE_USER_POOL_ID', 'ap-southeast-1_TESTPOOL1')
    vi.stubEnv('VITE_USER_POOL_CLIENT_ID', 'testclientid0000000000001')
    vi.resetModules()
    const fresh = await import('./auth')

    fresh.configureAuth()
    fresh.configureAuth()

    expect(amplify.configure).toHaveBeenCalledTimes(1)
    expect(amplify.configure).toHaveBeenCalledWith({
      Auth: {
        Cognito: {
          userPoolId: 'ap-southeast-1_TESTPOOL1',
          userPoolClientId: 'testclientid0000000000001',
          loginWith: { email: true },
        },
      },
    })
  })

  it('.env.example lists both variables with placeholder values', async () => {
    // Vite refuses to import .env files, so read it from disk (vitest runs from frontend/).
    const envExample = await readTextFile('.env.example')
    expect(envExample).toMatch(/^VITE_USER_POOL_ID=ap-southeast-1_X+$/m)
    expect(envExample).toMatch(/^VITE_USER_POOL_CLIENT_ID=x+$/m)
  })

  it('src/lib/auth.ts is the only app module that imports aws-amplify', () => {
    const sources = import.meta.glob<string>('/src/**/*.{ts,tsx}', {
      query: '?raw',
      import: 'default',
      eager: true,
    })
    const importsAmplify = /(?:from\s+|import\s*\(\s*|import\s+)['"](aws-amplify[^'"]*)['"]/g
    const offenders: string[] = []
    let authImports: string[] = []
    for (const [path, source] of Object.entries(sources)) {
      if (/\.test\.tsx?$/.test(path) || path.startsWith('/src/test/')) continue
      const found = [...source.matchAll(importsAmplify)].map((m) => m[1])
      if (path === '/src/lib/auth.ts') authImports = found
      else if (found.length > 0) offenders.push(path)
    }
    expect(offenders).toEqual([])
    expect(authImports.length).toBeGreaterThan(0)
    // Only the core configure entry point and the auth category.
    expect(new Set(authImports)).toEqual(new Set(['aws-amplify', 'aws-amplify/auth']))
  })
})

describe('signIn', () => {
  it('uses SRP with the trimmed email', async () => {
    amplify.signIn.mockResolvedValue(step('DONE'))
    await expect(auth.signIn('  test.nurse.a@example.test ', 'pw')).resolves.toBe('done')
    expect(amplify.signIn).toHaveBeenCalledWith({
      username: 'test.nurse.a@example.test',
      password: 'pw',
      options: { authFlowType: 'USER_SRP_AUTH' },
    })
  })

  it.each([
    ['CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED', 'newPasswordRequired'],
    ['CONFIRM_SIGN_IN_WITH_TOTP_CODE', 'totpCode'],
  ])('maps next step %s to %s', async (signInStep, expected) => {
    amplify.signIn.mockResolvedValue(step(signInStep))
    await expect(auth.signIn('a@example.test', 'pw')).resolves.toBe(expected)
  })

  it('treats unsupported challenges (e.g. SMS MFA) as a generic error', async () => {
    amplify.signIn.mockResolvedValue(step('CONFIRM_SIGN_IN_WITH_SMS_CODE'))
    await expect(auth.signIn('a@example.test', 'pw')).rejects.toMatchObject({ kind: 'generic' })
  })

  it('shows the same message for a wrong password and an unknown email (criterion 11)', async () => {
    amplify.signIn.mockRejectedValueOnce(cognitoError('NotAuthorizedException'))
    const wrongPassword = await auth.signIn('a@example.test', 'pw').catch((e: unknown) => e)
    amplify.signIn.mockRejectedValueOnce(cognitoError('UserNotFoundException'))
    const unknownEmail = await auth.signIn('b@example.test', 'pw').catch((e: unknown) => e)

    expect(wrongPassword).toBeInstanceOf(auth.AuthFlowError)
    expect(unknownEmail).toBeInstanceOf(auth.AuthFlowError)
    expect((wrongPassword as Error).message).toBe('Incorrect email or password.')
    expect((unknownEmail as Error).message).toBe('Incorrect email or password.')
  })

  it('never passes the Cognito error message through', async () => {
    amplify.signIn.mockRejectedValue(cognitoError('NetworkError'))
    const error = (await auth.signIn('a@example.test', 'pw').catch((e: unknown) => e)) as Error
    expect(error.message).toBe('Something went wrong. Please try again.')
    expect(error.message).not.toContain('example.test')
  })
})

describe('sign-in challenges', () => {
  it('completeNewPassword answers the challenge with the new password', async () => {
    amplify.confirmSignIn.mockResolvedValue(step('DONE'))
    await expect(auth.completeNewPassword('Test-Passw0rd!2026')).resolves.toBe('done')
    expect(amplify.confirmSignIn).toHaveBeenCalledWith({ challengeResponse: 'Test-Passw0rd!2026' })
  })

  it('completeNewPassword maps an expired session', async () => {
    amplify.confirmSignIn.mockRejectedValue(cognitoError('NotAuthorizedException'))
    await expect(auth.completeNewPassword('x')).rejects.toMatchObject({ kind: 'sessionExpired' })
  })

  it('confirmTotpCode maps a wrong code to "That code didn\'t work. Try again." (criterion 13)', async () => {
    amplify.confirmSignIn.mockRejectedValue(cognitoError('CodeMismatchException'))
    await expect(auth.confirmTotpCode('000000')).rejects.toMatchObject({
      kind: 'codeMismatch',
      message: "That code didn't work. Try again.",
    })
  })

  it('confirmTotpCode trims the code', async () => {
    amplify.confirmSignIn.mockResolvedValue(step('DONE'))
    await auth.confirmTotpCode(' 123456 ')
    expect(amplify.confirmSignIn).toHaveBeenCalledWith({ challengeResponse: '123456' })
  })
})

describe('session', () => {
  it('returns null when there is no ID token', async () => {
    amplify.fetchAuthSession.mockResolvedValue({ tokens: undefined })
    await expect(auth.getCurrentUser()).resolves.toBeNull()
  })

  it('returns null when the session cannot be read', async () => {
    amplify.fetchAuthSession.mockRejectedValue(cognitoError('NetworkError'))
    await expect(auth.getCurrentUser()).resolves.toBeNull()
  })

  it('reads groups from cognito:groups and name/email from attributes', async () => {
    amplify.fetchAuthSession.mockResolvedValue({
      tokens: { idToken: { payload: { 'cognito:groups': ['nurse', 42, 'family'] } } },
    })
    amplify.fetchUserAttributes.mockResolvedValue({
      name: 'Test Nurse A',
      email: 'test.nurse.a@example.test',
    })
    await expect(auth.getCurrentUser()).resolves.toEqual({
      name: 'Test Nurse A',
      email: 'test.nurse.a@example.test',
      groups: ['nurse', 'family'],
    })
  })

  it('returns no groups when the claim is missing', async () => {
    amplify.fetchAuthSession.mockResolvedValue({ tokens: { idToken: { payload: {} } } })
    amplify.fetchUserAttributes.mockResolvedValue({})
    await expect(auth.getCurrentUser()).resolves.toEqual({ name: '', email: '', groups: [] })
  })

  it('signOut calls Amplify signOut (criterion 16)', async () => {
    amplify.signOut.mockResolvedValue(undefined)
    await auth.signOut()
    expect(amplify.signOut).toHaveBeenCalledTimes(1)
  })
})

describe('forgot password (criterion 14)', () => {
  it.each([
    'UserNotFoundException',
    'InvalidParameterException',
    'NotAuthorizedException',
    'LimitExceededException',
    'CodeDeliveryFailureException',
  ])('resolves the same way when Cognito says %s', async (name) => {
    amplify.resetPassword.mockRejectedValue(cognitoError(name))
    await expect(auth.requestPasswordReset('a@example.test')).resolves.toBeUndefined()
  })

  it('resolves when the code is sent', async () => {
    amplify.resetPassword.mockResolvedValue({})
    await expect(auth.requestPasswordReset(' a@example.test ')).resolves.toBeUndefined()
    expect(amplify.resetPassword).toHaveBeenCalledWith({ username: 'a@example.test' })
  })

  it('rejects with the generic message on network errors', async () => {
    amplify.resetPassword.mockRejectedValue(cognitoError('NetworkError'))
    await expect(auth.requestPasswordReset('a@example.test')).rejects.toMatchObject({
      kind: 'generic',
    })
  })

  it.each(['CodeMismatchException', 'ExpiredCodeException'])(
    'confirmPasswordReset maps %s to "That code is invalid or has expired."',
    async (name) => {
      amplify.confirmResetPassword.mockRejectedValue(cognitoError(name))
      await expect(auth.confirmPasswordReset('a@example.test', '1', 'x')).rejects.toMatchObject({
        message: 'That code is invalid or has expired.',
      })
    },
  )

  it('confirmPasswordReset sends the trimmed email and code', async () => {
    amplify.confirmResetPassword.mockResolvedValue(undefined)
    await auth.confirmPasswordReset(' a@example.test ', ' 123456 ', 'Test-Passw0rd!2026')
    expect(amplify.confirmResetPassword).toHaveBeenCalledWith({
      username: 'a@example.test',
      confirmationCode: '123456',
      newPassword: 'Test-Passw0rd!2026',
    })
  })
})

describe('account settings (criterion 15)', () => {
  it('changePassword sends current and new password', async () => {
    amplify.updatePassword.mockResolvedValue(undefined)
    await auth.changePassword('old', 'new')
    expect(amplify.updatePassword).toHaveBeenCalledWith({ oldPassword: 'old', newPassword: 'new' })
  })

  it('changePassword maps a wrong current password', async () => {
    amplify.updatePassword.mockRejectedValue(cognitoError('NotAuthorizedException'))
    await expect(auth.changePassword('old', 'new')).rejects.toMatchObject({
      message: 'Your current password is incorrect.',
    })
  })

  it.each([
    [{ enabled: ['TOTP'], preferred: 'TOTP' }, true],
    [{ enabled: undefined }, false],
    [{ enabled: [] }, false],
  ])('isTotpEnabled(%j) is %s', async (preference, expected) => {
    amplify.fetchMFAPreference.mockResolvedValue(preference)
    await expect(auth.isTotpEnabled()).resolves.toBe(expected)
  })

  it('startTotpSetup returns the secret and an otpauth:// URI for issuer SilverCare', async () => {
    const getSetupUri = vi.fn(
      (issuer: string, account: string) =>
        new URL(`otpauth://totp/${issuer}:${account}?secret=TESTSECRET&issuer=${issuer}`),
    )
    amplify.setUpTOTP.mockResolvedValue({ sharedSecret: 'TESTSECRET', getSetupUri })
    const setup = await auth.startTotpSetup('test.nurse.a@example.test')
    expect(getSetupUri).toHaveBeenCalledWith('SilverCare', 'test.nurse.a@example.test')
    expect(setup.secret).toBe('TESTSECRET')
    expect(setup.uri).toMatch(/^otpauth:\/\/totp\//)
  })

  it('finishTotpSetup verifies the code, then sets TOTP as preferred', async () => {
    amplify.verifyTOTPSetup.mockResolvedValue(undefined)
    amplify.updateMFAPreference.mockResolvedValue(undefined)
    await auth.finishTotpSetup(' 123456 ')
    expect(amplify.verifyTOTPSetup).toHaveBeenCalledWith({ code: '123456' })
    expect(amplify.updateMFAPreference).toHaveBeenCalledWith({ totp: 'PREFERRED' })
    expect(amplify.verifyTOTPSetup.mock.invocationCallOrder[0]).toBeLessThan(
      amplify.updateMFAPreference.mock.invocationCallOrder[0],
    )
  })

  it('finishTotpSetup does not enable TOTP when the code is wrong', async () => {
    amplify.verifyTOTPSetup.mockRejectedValue(cognitoError('EnableSoftwareTokenMFAException'))
    await expect(auth.finishTotpSetup('000000')).rejects.toMatchObject({ kind: 'codeMismatch' })
    expect(amplify.updateMFAPreference).not.toHaveBeenCalled()
  })

  it('disableTotp turns TOTP off', async () => {
    amplify.updateMFAPreference.mockResolvedValue(undefined)
    await auth.disableTotp()
    expect(amplify.updateMFAPreference).toHaveBeenCalledWith({ totp: 'DISABLED' })
  })
})
