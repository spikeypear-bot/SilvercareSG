import type { AppView, CognitoGroup } from '../types/auth'

const FAMILY_GROUPS: readonly CognitoGroup[] = ['caregiver', 'family']

/**
 * Picks the main-page view from the user's Cognito groups.
 * Nurse wins over doctor (alert handling is the more urgent job), any staff group wins
 * over caregiver/family, otherwise no access.
 * This only decides what to show. The API enforces access.
 */
export function viewForGroups(groups: readonly string[]): AppView {
  if (groups.includes('nurse')) return 'nurse'
  if (groups.includes('doctor')) return 'doctor'
  if (groups.some((g) => (FAMILY_GROUPS as readonly string[]).includes(g))) return 'family'
  return 'no-access'
}

const ROLE_LABELS: Record<CognitoGroup, string> = {
  doctor: 'Doctor',
  nurse: 'Nurse',
  caregiver: 'Caregiver',
  family: 'Family',
}

/**
 * Order used to pick one role label when a user is in several groups. Same precedence
 * as `viewForGroups`, so the role badge matches the dashboard shown.
 */
const ROLE_ORDER: readonly CognitoGroup[] = ['nurse', 'doctor', 'caregiver', 'family']

/** Display label for the user's role, e.g. "Doctor". "No role" when none of the four groups. */
export function roleLabel(groups: readonly string[]): string {
  const group = ROLE_ORDER.find((g) => groups.includes(g))
  return group ? ROLE_LABELS[group] : 'No role'
}
