/** Cognito groups the app knows about (see docs/features/sign-in.md). */
export type CognitoGroup = 'doctor' | 'nurse' | 'caregiver' | 'family'

/** Which main page a signed-in user sees. Decided from groups; the API enforces access. */
export type AppView = 'staff' | 'family' | 'no-access'

/** The signed-in user's own profile, from Cognito attributes and the ID token. */
export interface AuthUser {
  name: string
  email: string
  groups: string[]
}

/** Next step of the login page after a sign-in call. */
export type SignInStep = 'done' | 'newPasswordRequired' | 'totpCode'

/** Error kinds the auth wrapper maps Cognito errors to. */
export type AuthErrorKind =
  | 'incorrectCredentials'
  | 'codeMismatch'
  | 'codeInvalidOrExpired'
  | 'incorrectCurrentPassword'
  | 'sessionExpired'
  | 'generic'

export interface TotpSetup {
  /** Shared secret, shown as text on the setup screen only. Never stored by the app. */
  secret: string
  /** otpauth:// URI, rendered as a QR code in the browser. */
  uri: string
}

/** Router state passed to /login (never in the URL). */
export interface LoginLocationState {
  notice?: 'passwordReset'
}
