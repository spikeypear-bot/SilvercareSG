/** Password rules matching the Cognito pool policy (docs/features/sign-in.md, criterion 2). */
export interface PasswordRule {
  id: string
  label: string
  test: (password: string) => boolean
}

// Characters Cognito treats as symbols.
const SYMBOL = /[\^$*.[\]{}()?"!@#%&/\\,><':;|_~`=+-]/

export const PASSWORD_RULES: readonly PasswordRule[] = [
  { id: 'length', label: 'At least 12 characters', test: (p) => p.length >= 12 },
  { id: 'lower', label: 'A lowercase letter', test: (p) => /[a-z]/.test(p) },
  { id: 'upper', label: 'An uppercase letter', test: (p) => /[A-Z]/.test(p) },
  { id: 'number', label: 'A number', test: (p) => /[0-9]/.test(p) },
  { id: 'symbol', label: 'A symbol, such as ! @ # $', test: (p) => SYMBOL.test(p) },
]

export function meetsPasswordRules(password: string): boolean {
  return PASSWORD_RULES.every((rule) => rule.test(password))
}
