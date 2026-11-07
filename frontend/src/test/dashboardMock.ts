/**
 * Controllable loader for `useDashboardData`. Usage in a dashboard test file:
 *
 *   vi.mock('./useDashboardData', async (importOriginal) => {
 *     const actual = await importOriginal<typeof import('./useDashboardData')>()
 *     const { dashboardLoader } = await import('../../test/dashboardMock')
 *     return {
 *       ...actual,
 *       useDashboardData: (view: DashboardView) => actual.useDashboardData(view, dashboardLoader),
 *     }
 *   })
 */
import { vi } from 'vitest'
import { FAMILY_LINKED_RESIDENT_IDS, createDemoData, type DemoDashboardData } from '../features/dashboard/demoData'
import type { DashboardLoader, DashboardView } from '../features/dashboard/useDashboardData'

/** Demo data without the 400 ms delay, filtered the same way as the real loader. */
export function demoDataFor(view: DashboardView, now: Date = new Date()): DemoDashboardData {
  const data = createDemoData(now)
  if (view === 'staff') return data
  const linked = new Set(FAMILY_LINKED_RESIDENT_IDS)
  return {
    residents: data.residents.filter((r) => linked.has(r.resident_id)),
    alerts: data.alerts.filter((a) => linked.has(a.resident_id)),
  }
}

export const dashboardLoader = vi.fn<DashboardLoader>()

export function resetDashboardLoader(): void {
  dashboardLoader.mockReset()
  dashboardLoader.mockImplementation(async (view) => demoDataFor(view))
}
