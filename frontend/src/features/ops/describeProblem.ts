import type { DeviceProblem } from '../../types/ops'
import { ALERT_TYPE_LABELS } from '../dashboard/labels'

/** "Offline, wearable-0007, resident res-0003": ops see no resident names. */
export function describeProblem(problem: DeviceProblem): string {
  const who = problem.resident_id === 'unassigned' ? 'not assigned' : `resident ${problem.resident_id}`
  return `${ALERT_TYPE_LABELS[problem.alert_type]}, ${problem.device_id ?? 'unknown device'}, ${who}`
}
