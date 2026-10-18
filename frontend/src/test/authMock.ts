/**
 * Mock functions for the `src/lib/auth.ts` boundary. Tests never call real Cognito.
 *
 * Usage in a test file (path relative to that file):
 *
 *   vi.mock('../../lib/auth', async (importOriginal) => {
 *     const actual = await importOriginal<typeof import('../../lib/auth')>()
 *     const { authMock } = await import('../../test/authMock')
 *     return { ...actual, ...authMock }
 *   })
 *
 * `AUTH_MESSAGES` and `AuthFlowError` stay real so `instanceof` checks behave.
 */
import { vi } from 'vitest'
import type * as Auth from '../lib/auth'

export const authMock = {
  configureAuth: vi.fn<typeof Auth.configureAuth>(),
  getCurrentUser: vi.fn<typeof Auth.getCurrentUser>(),
  signIn: vi.fn<typeof Auth.signIn>(),
  completeNewPassword: vi.fn<typeof Auth.completeNewPassword>(),
  confirmTotpCode: vi.fn<typeof Auth.confirmTotpCode>(),
  signOut: vi.fn<typeof Auth.signOut>(),
  requestPasswordReset: vi.fn<typeof Auth.requestPasswordReset>(),
  confirmPasswordReset: vi.fn<typeof Auth.confirmPasswordReset>(),
  changePassword: vi.fn<typeof Auth.changePassword>(),
  isTotpEnabled: vi.fn<typeof Auth.isTotpEnabled>(),
  startTotpSetup: vi.fn<typeof Auth.startTotpSetup>(),
  finishTotpSetup: vi.fn<typeof Auth.finishTotpSetup>(),
  disableTotp: vi.fn<typeof Auth.disableTotp>(),
}

/** Resets every mock to a signed-out, everything-succeeds default. */
export function resetAuthMock(): void {
  Object.values(authMock).forEach((fn) => fn.mockReset())
  authMock.getCurrentUser.mockResolvedValue(null)
  authMock.signOut.mockResolvedValue(undefined)
  authMock.isTotpEnabled.mockResolvedValue(false)
}
