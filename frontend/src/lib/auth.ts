/**
 * Amplify Auth wrapper. This is the only module that imports from `aws-amplify`.
 *
 * - Configured from VITE_USER_POOL_ID / VITE_USER_POOL_CLIENT_ID (public IDs, not secrets).
 * - Amplify stores tokens itself; this module never stores, returns, or logs them.
 * - Cognito errors are mapped to the user-facing messages in docs/features/sign-in.md.
 *   Error details are never logged (they can contain emails or codes).
 */
import { Amplify } from 'aws-amplify'
import {
  confirmResetPassword as amplifyConfirmResetPassword,
  confirmSignIn as amplifyConfirmSignIn,
  fetchAuthSession,
  fetchMFAPreference,
  fetchUserAttributes,
  resetPassword as amplifyResetPassword,
  setUpTOTP,
  signIn as amplifySignIn,
  signOut as amplifySignOut,
  updateMFAPreference,
  updatePassword as amplifyUpdatePassword,
  verifyTOTPSetup,
  type SignInOutput,
} from 'aws-amplify/auth'
import type { AuthErrorKind, AuthUser, SignInStep, TotpSetup } from '../types/auth'

export const AUTH_MESSAGES: Record<AuthErrorKind, string> = {
  incorrectCredentials: 'Incorrect email or password.',
  codeMismatch: "That code didn't work. Try again.",
  codeInvalidOrExpired: 'That code is invalid or has expired.',
  incorrectCurrentPassword: 'Your current password is incorrect.',
  sessionExpired: 'Your sign-in session has expired. Please sign in again.',
  generic: 'Something went wrong. Please try again.',
}

/** Error thrown by every function in this module. `message` is safe to show. */
export class AuthFlowError extends Error {
  readonly kind: AuthErrorKind

  constructor(kind: AuthErrorKind) {
    super(AUTH_MESSAGES[kind])
    this.name = 'AuthFlowError'
    this.kind = kind
  }
}

const TOTP_ISSUER = 'SilverCare'

let configured = false

export function configureAuth(): void {
  if (configured) return
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: import.meta.env.VITE_USER_POOL_ID ?? '',
        userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID ?? '',
        loginWith: { email: true },
      },
    },
  })
  configured = true
}

/** Cognito/Amplify error name, without reading or exposing the message. */
function errorName(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'name' in error) {
    const { name } = error as { name: unknown }
    if (typeof name === 'string') return name
  }
  return ''
}

function mapError(error: unknown, mapping: Record<string, AuthErrorKind>): AuthFlowError {
  if (error instanceof AuthFlowError) return error
  return new AuthFlowError(mapping[errorName(error)] ?? 'generic')
}

function toStep(output: SignInOutput): SignInStep {
  switch (output.nextStep.signInStep) {
    case 'DONE':
      return 'done'
    case 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED':
      return 'newPasswordRequired'
    case 'CONFIRM_SIGN_IN_WITH_TOTP_CODE':
      return 'totpCode'
    default:
      // Other challenges (SMS/email MFA, forced reset, TOTP setup during sign-in) are not
      // used by this user pool.
      throw new AuthFlowError('generic')
  }
}

// ---------------------------------------------------------------------------
// Session

/** Returns the signed-in user, or null when signed out. */
export async function getCurrentUser(): Promise<AuthUser | null> {
  let groups: string[]
  try {
    const session = await fetchAuthSession()
    const idToken = session.tokens?.idToken
    if (!idToken) return null
    const claim = idToken.payload['cognito:groups']
    groups = Array.isArray(claim) ? claim.filter((g): g is string => typeof g === 'string') : []
  } catch {
    return null
  }

  try {
    const attributes = await fetchUserAttributes()
    return {
      name: attributes.name ?? '',
      email: attributes.email ?? '',
      groups,
    }
  } catch (error) {
    throw mapError(error, {})
  }
}

// ---------------------------------------------------------------------------
// Sign in / out

export async function signIn(email: string, password: string): Promise<SignInStep> {
  try {
    const output = await amplifySignIn({
      username: email.trim(),
      password,
      options: { authFlowType: 'USER_SRP_AUTH' },
    })
    return toStep(output)
  } catch (error) {
    // Same message for wrong password and unknown email (criterion 11).
    throw mapError(error, {
      NotAuthorizedException: 'incorrectCredentials',
      UserNotFoundException: 'incorrectCredentials',
    })
  }
}

/** Answers the NEW_PASSWORD_REQUIRED challenge for admin-created users. */
export async function completeNewPassword(newPassword: string): Promise<SignInStep> {
  try {
    const output = await amplifyConfirmSignIn({ challengeResponse: newPassword })
    return toStep(output)
  } catch (error) {
    throw mapError(error, {
      NotAuthorizedException: 'sessionExpired',
      ExpiredCodeException: 'sessionExpired',
    })
  }
}

/** Answers the TOTP challenge with a 6-digit code. */
export async function confirmTotpCode(code: string): Promise<SignInStep> {
  try {
    const output = await amplifyConfirmSignIn({ challengeResponse: code.trim() })
    return toStep(output)
  } catch (error) {
    throw mapError(error, {
      CodeMismatchException: 'codeMismatch',
      EnableSoftwareTokenMFAException: 'codeMismatch',
      InvalidParameterException: 'codeMismatch',
      NotAuthorizedException: 'sessionExpired',
      ExpiredCodeException: 'sessionExpired',
    })
  }
}

/** Signs out and revokes the refresh token (Amplify clears its token storage). */
export async function signOut(): Promise<void> {
  try {
    await amplifySignOut()
  } catch (error) {
    throw mapError(error, {})
  }
}

// ---------------------------------------------------------------------------
// Forgot password

/**
 * Sends a reset code. Resolves the same way whether or not the account exists
 * (criterion 14); only network or unexpected failures reject.
 */
export async function requestPasswordReset(email: string): Promise<void> {
  try {
    await amplifyResetPassword({ username: email.trim() })
  } catch (error) {
    const neutral = new Set([
      'UserNotFoundException',
      'InvalidParameterException',
      'NotAuthorizedException',
      'LimitExceededException',
      'CodeDeliveryFailureException',
    ])
    if (neutral.has(errorName(error))) return
    throw mapError(error, {})
  }
}

export async function confirmPasswordReset(
  email: string,
  code: string,
  newPassword: string,
): Promise<void> {
  try {
    await amplifyConfirmResetPassword({
      username: email.trim(),
      confirmationCode: code.trim(),
      newPassword,
    })
  } catch (error) {
    throw mapError(error, {
      CodeMismatchException: 'codeInvalidOrExpired',
      ExpiredCodeException: 'codeInvalidOrExpired',
      UserNotFoundException: 'codeInvalidOrExpired',
    })
  }
}

// ---------------------------------------------------------------------------
// Account settings

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  try {
    await amplifyUpdatePassword({ oldPassword, newPassword })
  } catch (error) {
    throw mapError(error, { NotAuthorizedException: 'incorrectCurrentPassword' })
  }
}

/** True when the authenticator app (TOTP) is enabled for the signed-in user. */
export async function isTotpEnabled(): Promise<boolean> {
  try {
    const preference = await fetchMFAPreference()
    return preference.enabled?.includes('TOTP') ?? false
  } catch (error) {
    throw mapError(error, {})
  }
}

/** Starts authenticator-app setup. The caller must not persist the secret. */
export async function startTotpSetup(accountName: string): Promise<TotpSetup> {
  try {
    const details = await setUpTOTP()
    return {
      secret: details.sharedSecret,
      uri: details.getSetupUri(TOTP_ISSUER, accountName).toString(),
    }
  } catch (error) {
    throw mapError(error, {})
  }
}

/** Verifies the first code from the app, then makes TOTP the preferred MFA method. */
export async function finishTotpSetup(code: string): Promise<void> {
  try {
    await verifyTOTPSetup({ code: code.trim() })
  } catch (error) {
    throw mapError(error, {
      CodeMismatchException: 'codeMismatch',
      EnableSoftwareTokenMFAException: 'codeMismatch',
      InvalidParameterException: 'codeMismatch',
    })
  }
  try {
    await updateMFAPreference({ totp: 'PREFERRED' })
  } catch (error) {
    throw mapError(error, {})
  }
}

export async function disableTotp(): Promise<void> {
  try {
    await updateMFAPreference({ totp: 'DISABLED' })
  } catch (error) {
    throw mapError(error, {})
  }
}
