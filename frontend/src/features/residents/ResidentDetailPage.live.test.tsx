/**
 * The resident page's Resolve against API data: `src/lib/api.ts` is mocked (getResident,
 * getThresholdHistory, resolveAlertApi), so the real useResidentDetail runs its live path
 * and nothing reaches the network. "Resolved: ..." is announced and focus moves to the
 * Alert history heading only after the 202; on an error nothing is announced.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { AuthUser } from '../../types/auth'
import type { ApiResidentAlert, ApiResidentDetail, ApiThresholdChange } from '../../types/resident'

const api = vi.hoisted(() => ({
  getResident: vi.fn<(id: string) => Promise<ApiResidentDetail>>(),
  getThresholdHistory: vi.fn<(id: string) => Promise<ApiThresholdChange[]>>(),
  resolveAlertApi: vi.fn<typeof import('../../lib/api').resolveAlertApi>(),
}))
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    getResident: api.getResident,
    getThresholdHistory: api.getThresholdHistory,
    resolveAlertApi: api.resolveAlertApi,
  }
})

import { ApiError, type AlertActionResponse } from '../../lib/api'
import { ResidentDetailPage } from './ResidentDetailPage'

const NURSE: AuthUser = { name: 'Test Nurse D', email: 'test.nurse.d@example.test', groups: ['nurse'] }
const C = 'Fall detected, Test Resident C'
const NOTE = 'Synthetic note: resident checked, false alarm.'
const ACCEPTED: AlertActionResponse = { alert_id: 'alert-test-001', requested_status: 'resolved' }

const OPEN_ALERT: ApiResidentAlert = {
  alert_id: 'alert-test-001',
  resident_id: 'resident-test-003',
  device_id: 'wearable-test-003',
  alert_type: 'fall',
  severity: 'critical',
  status: 'open',
  event_ts: new Date(Date.now() - 5 * 60_000).toISOString(),
  acknowledged_at: null,
  acknowledged_by_role: null,
  acknowledged_by: null,
  ack_note: null,
  escalated_at: null,
  escalates_at: null,
  resolved_at: null,
  resolved_by: null,
  cancelled_at: null,
  cancelled_by_role: null,
  cancelled_by: null,
}

const DETAIL: ApiResidentDetail = {
  resident: {
    resident_id: 'resident-test-003',
    display_name: 'Test Resident C',
    home_id: 'home-test-003',
    status: 'attention',
    status_text: 'Synthetic status.',
    wearable: null,
    bedroom: null,
    vitals: [],
  },
  wearable: null,
  bedroom: null,
  alerts: [OPEN_ALERT],
  thresholds: {
    hr_bpm: { min: 50, max: 120 },
    spo2_pct: { min: 92 },
    skin_temp_c: { min: 30, max: 38 },
    updated_at: null,
    updated_by: null,
  },
  threshold_history: [],
}

beforeEach(() => {
  api.getResident.mockReset()
  api.getResident.mockResolvedValue(DETAIL)
  api.getThresholdHistory.mockReset()
  api.getThresholdHistory.mockResolvedValue([])
  api.resolveAlertApi.mockReset()
  api.resolveAlertApi.mockRejectedValue(new Error('resolveAlertApi not set up for this test'))
})

async function renderLoaded() {
  const user = userEvent.setup()
  renderWithAuth(<ResidentDetailPage />, {
    user: NURSE,
    path: '/residents/resident-test-003',
    route: '/residents/:id',
  })
  await user.click(await screen.findByRole('tab', { name: /^Alerts/ }))
  return user
}

const announcer = () => screen.getByRole('status')
const historyHeading = () => screen.getByRole('heading', { level: 2, name: /^Alert history/ })
const history = () => screen.getByRole('region', { name: /^Alert history/ })
const resolveButton = () => screen.getByRole('button', { name: `Resolve: ${C}` })

async function submitResolve(user: ReturnType<typeof userEvent.setup>) {
  await user.click(resolveButton())
  await user.type(screen.getByRole('textbox', { name: 'Closing note (required)' }), NOTE)
  await user.click(resolveButton())
}

describe('resident page resolve with API data', () => {
  it('announces and moves focus to Alert history only after the 202, never with the note', async () => {
    const user = await renderLoaded()
    const request = deferred<AlertActionResponse>()
    api.resolveAlertApi.mockReturnValue(request.promise)

    await submitResolve(user)
    expect(api.resolveAlertApi).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ resident_id: 'resident-test-003', alert_id: 'alert-test-001' }),
      NOTE,
    )
    expect(within(history()).getByText('Open')).toBeInTheDocument()
    expect(announcer()).toBeEmptyDOMElement()
    await waitFor(() => expect(resolveButton()).toHaveFocus())

    request.resolve(ACCEPTED)
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${C}.`))
    expect(announcer()).not.toHaveTextContent('Synthetic note')
    await waitFor(() => expect(historyHeading()).toHaveFocus())
    expect(within(history()).getByText('Resolved')).toBeInTheDocument()
    expect(history()).toHaveTextContent('Resolved by Test Nurse D, just now')
    expect(document.body).not.toHaveTextContent(NOTE)
  })

  it('on an error: the notice, no announcement, still open, focus stays on Resolve', async () => {
    const user = await renderLoaded()
    api.resolveAlertApi.mockRejectedValue(new ApiError(409, 'conflict'))
    await submitResolve(user)

    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(
      'This alert has already been dealt with. Reload the page to see its current state.',
    )
    expect(announcer()).toBeEmptyDOMElement()
    expect(within(history()).getByText('Open')).toBeInTheDocument()
    await waitFor(() => expect(resolveButton()).toHaveFocus())
    expect(historyHeading()).not.toHaveFocus()
  })

  it('a 400 says the note was not accepted', async () => {
    const user = await renderLoaded()
    api.resolveAlertApi.mockRejectedValue(new ApiError(400, 'note'))
    await submitResolve(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('The note was not accepted. Check it and try again.')
    expect(announcer()).toBeEmptyDOMElement()
  })
})
