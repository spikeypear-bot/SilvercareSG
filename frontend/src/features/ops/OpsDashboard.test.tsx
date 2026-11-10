/**
 * The ops view against mocked API data: `src/lib/api.ts` is mocked (getDeviceProblems,
 * acknowledgeAlertApi), so nothing reaches the network. The server does the filtering
 * (lambdas/api-ops/tests/test_filtering.py); these tests cover what ops see and do.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { DeviceProblem } from '../../types/ops'

const api = vi.hoisted(() => ({
  getDeviceProblems: vi.fn<() => Promise<DeviceProblem[]>>(),
  acknowledgeAlertApi: vi.fn<typeof import('../../lib/api').acknowledgeAlertApi>(),
}))
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, getDeviceProblems: api.getDeviceProblems, acknowledgeAlertApi: api.acknowledgeAlertApi }
})

import { ApiError } from '../../lib/api'
import { MainPage } from '../dashboard/MainPage'
import { OpsDashboard } from './OpsDashboard'

function problem(overrides: Partial<DeviceProblem> = {}): DeviceProblem {
  return {
    resident_id: 'res-0001',
    sk: 'ALERT#2026-11-07T02:00:00.000Z#alert-ops-001',
    alert_id: 'alert-ops-001',
    alert_type: 'device_offline',
    severity: 'warning',
    device_id: 'bedroom-0001',
    event_ts: '2026-11-07T02:00:00.000Z',
    status: 'open',
    details: { last_seen: '2026-11-07T01:45:00.000Z' },
    acknowledged_at: null,
    acknowledged_by_role: null,
    acknowledged_by: null,
    resolved_at: null,
    resolved_by_role: null,
    ...overrides,
  }
}

const PROBLEMS = [
  problem(),
  problem({
    alert_id: 'alert-ops-002',
    alert_type: 'battery_low',
    resident_id: 'res-0002',
    device_id: 'wearable-0002',
    details: { battery_pct: 15 },
  }),
  problem({
    alert_id: 'alert-ops-003',
    alert_type: 'not_worn',
    device_id: 'wearable-0001',
    status: 'acknowledged',
    details: { since: '2026-11-07T01:00:00.000Z' },
    acknowledged_at: '2026-11-07T02:10:00.000Z',
    acknowledged_by_role: 'caregiver',
    acknowledged_by: 'Test Caregiver A',
  }),
  problem({ alert_id: 'alert-ops-004', resident_id: 'unassigned', device_id: 'wearable-0009' }),
]

beforeEach(() => {
  api.getDeviceProblems.mockReset()
  api.getDeviceProblems.mockResolvedValue(PROBLEMS)
  api.acknowledgeAlertApi.mockReset()
  api.acknowledgeAlertApi.mockRejectedValue(new Error('acknowledgeAlertApi not set up for this test'))
})

async function renderLoaded() {
  const user = userEvent.setup()
  renderWithAuth(<OpsDashboard />, { user: TEST_USERS.ops })
  await screen.findByRole('heading', { level: 2, name: /^All device problems/ })
  return user
}

it('an ops user gets the ops dashboard on /', async () => {
  renderWithAuth(<MainPage />, { user: TEST_USERS.ops })
  expect(await screen.findByRole('heading', { level: 1, name: 'Ops dashboard' })).toBeInTheDocument()
  await screen.findByRole('heading', { level: 2, name: /^All device problems/ })
})

it('a nurse who is also in ops gets the nurse dashboard, not the ops one', async () => {
  renderWithAuth(<MainPage />, { user: { ...TEST_USERS.nurse, groups: ['ops', 'nurse'] } })
  expect(await screen.findByRole('heading', { level: 1, name: 'Nurse dashboard' })).toBeInTheDocument()
  expect(api.getDeviceProblems).not.toHaveBeenCalled()
})

it('shows every device problem with counts, details, and no staff tabs or demo banner', async () => {
  await renderLoaded()
  expect(screen.getByRole('heading', { level: 2, name: 'All device problems (4)' })).toBeInTheDocument()
  expect(screen.getByText('Device offline: bedroom-0001')).toBeInTheDocument()
  expect(screen.getByText('Battery 15%')).toBeInTheDocument()
  expect(screen.getByText(/Not assigned to a resident/)).toBeInTheDocument()
  expect(screen.getByText(/Acknowledged by Test Caregiver A/)).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: 'Staff area' })).not.toBeInTheDocument()
  expect(screen.queryByRole('note', { name: 'Demo data' })).not.toBeInTheDocument()
  // Residents by opaque ID only: no resident page links for ops (the API answers 403).
  expect(screen.queryByRole('link', { name: /res-0001/ })).not.toBeInTheDocument()
})

it('the tiles filter by type', async () => {
  const user = await renderLoaded()
  const group = screen.getByRole('group', { name: 'Choose which device problems the list shows' })
  await user.click(within(group).getByRole('button', { name: /Low battery/ }))
  expect(screen.getByRole('heading', { level: 2, name: 'Low battery (1)' })).toBeInTheDocument()
  expect(screen.queryByText('Device offline: bedroom-0001')).not.toBeInTheDocument()
})

it('Acknowledge is offered only on open problems', async () => {
  await renderLoaded()
  expect(screen.getAllByRole('button', { name: /^Acknowledge/ })).toHaveLength(3)
  expect(screen.queryByRole('button', { name: 'Acknowledge Wearable not worn on wearable-0001' })).not.toBeInTheDocument()
})

it('Acknowledge sends no note and shows "the ops team" once accepted', async () => {
  api.acknowledgeAlertApi.mockResolvedValue({ alert_id: 'alert-ops-002', requested_status: 'acknowledged' })
  const user = await renderLoaded()
  await user.click(screen.getByRole('button', { name: 'Acknowledge Low battery on wearable-0002' }))
  expect(api.acknowledgeAlertApi).toHaveBeenCalledWith(expect.objectContaining({ alert_id: 'alert-ops-002' }), null)
  expect(await screen.findByText(/Acknowledged by the ops team/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Acknowledge Low battery on wearable-0002' })).not.toBeInTheDocument()
})

it('a refused acknowledge shows an error and keeps the button', async () => {
  api.acknowledgeAlertApi.mockRejectedValue(new ApiError(409, 'conflict'))
  const user = await renderLoaded()
  await user.click(screen.getByRole('button', { name: 'Acknowledge Low battery on wearable-0002' }))
  expect(await screen.findByText("We couldn't acknowledge that device problem.")).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Acknowledge Low battery on wearable-0002' })).toBeInTheDocument()
})

it('shows a loading state, then an error with Try again', async () => {
  const pending = deferred<DeviceProblem[]>()
  api.getDeviceProblems.mockReturnValueOnce(pending.promise)
  renderWithAuth(<OpsDashboard />, { user: TEST_USERS.ops })
  expect(screen.getByRole('status')).toHaveTextContent('Loading device problems')
  pending.reject(new ApiError(500, 'internal_error'))
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent("We couldn't load the device problems.")
  await userEvent.setup().click(within(alert).getByRole('button', { name: 'Try again' }))
  await waitFor(() => expect(api.getDeviceProblems).toHaveBeenCalledTimes(2))
})

it('an empty list says so', async () => {
  api.getDeviceProblems.mockResolvedValue([])
  renderWithAuth(<OpsDashboard />, { user: TEST_USERS.ops })
  expect(await screen.findByText('No device problems')).toBeInTheDocument()
})
