import type { AppView, CognitoGroup } from '../types/auth'

const STAFF_GROUPS: readonly CognitoGroup[] = ['doctor', 'nurse']
const FAMILY_GROUPS: readonly CognitoGroup[] = ['caregiver', 'family']

/**
 * Picks the main-page view from the user's Cognito groups.
 * Any staff group wins; otherwise any caregiver/family group; otherwise no access.
 * This only decides what to show. The API enforces access.
 */
export function viewForGroups(groups: readonly string[]): AppView {
  if (groups.some((g) => (STAFF_GROUPS as readonly string[]).includes(g))) return 'staff'
  if (groups.some((g) => (FAMILY_GROUPS as readonly string[]).includes(g))) return 'family'
  return 'no-access'
}

const ROLE_LABELS: Record<CognitoGroup, string> = {
  doctor: 'Doctor',
  nurse: 'Nurse',
  caregiver: 'Caregiver',
  family: 'Family',
}

/** Order used to pick one role label when a user is in several groups. */
const ROLE_ORDER: readonly CognitoGroup[] = ['doctor', 'nurse', 'caregiver', 'family']

/** Display label for the user's role, e.g. "Doctor". "No role" when none of the four groups. */
export function roleLabel(groups: readonly string[]): string {
  const group = ROLE_ORDER.find((g) => groups.includes(g))
  return group ? ROLE_LABELS[group] : 'No role'
}
