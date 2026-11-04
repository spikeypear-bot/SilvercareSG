import type { AppView, CognitoGroup } from '../types/auth'

/**
 * Picks the main-page view from the user's Cognito groups.
 * Nurse wins over doctor (alert handling is the more urgent job), any staff group wins
 * over caregiver, otherwise no access. `family` alone means no access: the group is
 * removed (docs/features/alert-routing.md) and the API refuses it.
 * This only decides what to show. The API enforces access.
 */
export function viewForGroups(groups: readonly string[]): AppView {
  if (groups.includes('nurse')) return 'nurse'
  if (groups.includes('doctor')) return 'doctor'
  if (groups.includes('caregiver')) return 'family'
  return 'no-access'
}

const ROLE_LABELS: Record<CognitoGroup, string> = {
  doctor: 'Doctor',
  nurse: 'Nurse',
  caregiver: 'Caregiver',
}

/**
 * Order used to pick one role label when a user is in several groups. Same precedence
 * as `viewForGroups`, so the role badge matches the dashboard shown.
 */
const ROLE_ORDER: readonly CognitoGroup[] = ['nurse', 'doctor', 'caregiver']

/** Display label for the user's role, e.g. "Doctor". "No role" when none of the three groups. */
export function roleLabel(groups: readonly string[]): string {
  const group = ROLE_ORDER.find((g) => groups.includes(g))
  return group ? ROLE_LABELS[group] : 'No role'
}
