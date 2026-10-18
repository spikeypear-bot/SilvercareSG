import { createContext } from 'react'
import type { AppView, AuthUser } from '../../types/auth'

export type AuthStatus = 'loading' | 'signedIn' | 'signedOut'

export interface AuthContextValue {
  status: AuthStatus
  user: AuthUser | null
  /** Main-page view for the signed-in user ('no-access' when signed out). */
  view: AppView
  /** Display label for the user's role, e.g. "Nurse". */
  role: string
  /** Re-reads the session from Amplify, e.g. after sign-in completes. */
  refresh: () => Promise<void>
  /** Signs out through Amplify and clears the user. */
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
