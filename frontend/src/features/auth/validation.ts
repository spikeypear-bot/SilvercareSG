/** Basic client-side email check; Cognito does the real validation. */
export function validateEmail(email: string): string | undefined {
  const value = email.trim()
  if (!value) return 'Enter your email address.'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return 'Enter a valid email address.'
  return undefined
}
