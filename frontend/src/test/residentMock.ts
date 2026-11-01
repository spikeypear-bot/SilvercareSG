/**
 * Controllable loader for `useResidentDetail`. Usage in a test file under src/features/<x>/:
 *
 *   vi.mock('../dashboard/useDashboardData', async (importOriginal) => {
 *     const actual = await importOriginal<typeof import('../dashboard/useDashboardData')>()
 *     const { residentDetailLoader } = await import('../../test/residentMock')
 *     return {
 *       ...actual,
 *       useResidentDetail: (id: string) => actual.useResidentDetail(id, residentDetailLoader),
 *     }
 *   })
 */
import { vi } from 'vitest'
import { createResidentDetail } from '../features/dashboard/demoData'
import type { ResidentDetailLoader } from '../features/dashboard/useDashboardData'
import type { ResidentDetail } from '../types/resident'

export const residentDetailLoader = vi.fn<ResidentDetailLoader>()

/** Demo detail without the 400 ms delay. */
export function resetResidentDetailLoader(): void {
  residentDetailLoader.mockReset()
  residentDetailLoader.mockImplementation(async (id) => createResidentDetail(id, new Date()))
}

/** The demo detail for `id`; throws if the ID is unknown (a test setup mistake). */
export function demoDetail(id: string, now: Date = new Date()): ResidentDetail {
  const detail = createResidentDetail(id, now)
  if (!detail) throw new Error(`No demo resident ${id}`)
  return detail
}
