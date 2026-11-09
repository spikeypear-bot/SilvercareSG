import { renderHook, waitFor } from '@testing-library/react'
import { apiResident } from '../../test/apiDashboard'
import type { ApiDashboardResponse } from '../../types/dashboard'
import type { ApiMyResidentsResponse } from '../../types/resident'

// Which channels each view hears (docs/features/live-alerts.md, criterion 19).
const mocks = vi.hoisted(() => ({
  getDashboard: vi.fn<() => Promise<ApiDashboardResponse>>(),
  getMyResidents: vi.fn<() => Promise<ApiMyResidentsResponse>>(),
  getResident: vi.fn(),
  getThresholdHistory: vi.fn(),
  useLiveRefresh: vi.fn<(channels: readonly string[], onPing: () => void) => void>(),
}))
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  getDashboard: mocks.getDashboard,
  getMyResidents: mocks.getMyResidents,
  getResident: mocks.getResident,
  getThresholdHistory: mocks.getThresholdHistory,
}))
vi.mock('../../lib/live', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/live')>()),
  useLiveRefresh: mocks.useLiveRefresh,
}))

import { useDashboardData, useResidentDetail } from './useDashboardData'

const lastChannels = () => mocks.useLiveRefresh.mock.lastCall?.[0]

beforeEach(() => {
  mocks.useLiveRefresh.mockReset()
  mocks.getDashboard.mockResolvedValue({ residents: [apiResident(1)], alerts: [] })
  const linked = (n: number) => ({
    ...apiResident(n),
    vitals: [{ ts: '2026-11-07T02:00:00.000Z', hr_bpm: 72, spo2_pct: 97, skin_temp_c: 33.4, activity_pct: 12 }],
  })
  mocks.getMyResidents.mockResolvedValue({ residents: [linked(1), linked(2)], alerts: [] })
  mocks.getResident.mockRejectedValue(new Error('not needed'))
  mocks.getThresholdHistory.mockRejectedValue(new Error('not needed'))
})

it('staff dashboards hear every resident in both namespaces', async () => {
  renderHook(() => useDashboardData('staff'))
  await waitFor(() => expect(mocks.getDashboard).toHaveBeenCalled())
  expect(lastChannels()).toEqual(['/health/*', '/device/*'])
})

it('the caregiver view hears only its loaded residents, once loaded', async () => {
  renderHook(() => useDashboardData('family'))
  expect(lastChannels()).toEqual([])
  await waitFor(() =>
    expect(lastChannels()).toEqual([
      '/health/resident-test-001',
      '/device/resident-test-001',
      '/health/resident-test-002',
      '/device/resident-test-002',
    ]),
  )
})

it('the resident page hears that resident only', () => {
  renderHook(() => useResidentDetail('res-0007'))
  expect(lastChannels()).toEqual(['/health/res-0007', '/device/res-0007'])
})

it('demo loaders (tests, demos) never subscribe', async () => {
  const loader = vi.fn().mockResolvedValue({ residents: [], alerts: [] })
  renderHook(() => useDashboardData('staff', loader))
  await waitFor(() => expect(loader).toHaveBeenCalled())
  expect(lastChannels()).toEqual([])
})

it('a ping refetches through the hook', async () => {
  const { result } = renderHook(() => useDashboardData('staff'))
  await waitFor(() => expect(result.current.data).not.toBeNull())
  const onPing = mocks.useLiveRefresh.mock.lastCall?.[1]
  onPing?.()
  await waitFor(() => expect(mocks.getDashboard).toHaveBeenCalledTimes(2))
})
