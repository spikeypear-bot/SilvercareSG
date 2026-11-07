/**
 * The nurse dashboard's Resolve against API data: `src/lib/api.ts` is mocked
 * (getDashboard, resolveAlertApi), so the real useDashboardData runs its live path and
 * nothing reaches the network. The announcement and the focus move come only after the
 * API accepts the resolve (202); on an error nothing is announced and the alert stays.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiAlert, apiResident } from '../../test/apiDashboard'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { ApiDashboardResponse } from '../../types/dashboard'

const api = vi.hoisted(() => ({
  getDashboard: vi.fn<() => Promise<ApiDashboardResponse>>(),
  resolveAlertApi: vi.fn<typeof import('../../lib/api').resolveAlertApi>(),
}))
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, getDashboard: api.getDashboard, resolveAlertApi: api.resolveAlertApi }
})

import { ApiError, type AlertActionResponse } from '../../lib/api'
import { NurseDashboard } from './NurseDashboard'

const A = 'Fall detected, Test Resident A'
const NOTE = 'Synthetic note: resident checked, false alarm.'
const ACCEPTED: AlertActionResponse = { alert_id: 'alert-test-001', requested_status: 'resolved' }

function body(): ApiDashboardResponse {
  return {
    residents: [apiResident(1), apiResident(2)],
    alerts: [
      apiAlert(),
      apiAlert({
        alert_id: 'alert-test-002',
        resident_id: 'resident-test-002',
        alert_type: 'hr_high',
        status: 'acknowledged',
        acknowledged_at: '2026-11-07T02:01:00Z',
      }),
    ],
  }
}

beforeEach(() => {
  api.getDashboard.mockReset()
  api.getDashboard.mockResolvedValue(body())
  api.resolveAlertApi.mockReset()
  api.resolveAlertApi.mockRejectedValue(new Error('resolveAlertApi not set up for this test'))
})

async function renderLoaded() {
  const user = userEvent.setup()
  renderWithAuth(<NurseDashboard />, { user: TEST_USERS.nurse })
  await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
  return user
}

const announcer = () => screen.getByRole('status')
const inboxHeading = () => screen.getByRole('heading', { level: 2, name: /^Open alerts/ })
const resolveButton = () => screen.getByRole('button', { name: `Resolve: ${A}` })
const inbox = () => screen.getByRole('region', { name: /^Open alerts/ })

async function submitResolve(user: ReturnType<typeof userEvent.setup>, note = NOTE) {
  await user.click(resolveButton())
  await user.type(screen.getByRole('textbox', { name: 'Closing note (required)' }), note)
  await user.click(resolveButton())
}

describe('nurse dashboard resolve with API data', () => {
  it('sends the note; announces and moves focus only after the 202, never with the note', async () => {
    const user = await renderLoaded()
    const request = deferred<AlertActionResponse>()
    api.resolveAlertApi.mockReturnValue(request.promise)

    await submitResolve(user)
    expect(api.resolveAlertApi).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ resident_id: 'resident-test-001', alert_id: 'alert-test-001' }),
      NOTE,
    )
    // In flight: still listed and open, nothing announced, focus on the row's Resolve.
    expect(inboxHeading()).toHaveTextContent('Open alerts (2)')
    expect(within(inbox()).getAllByText('Open').length).toBeGreaterThan(0)
    expect(announcer()).toBeEmptyDOMElement()
    await waitFor(() => expect(resolveButton()).toHaveFocus())
    expect(inboxHeading()).not.toHaveFocus()

    request.resolve(ACCEPTED)
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${A}.`))
    expect(announcer()).not.toHaveTextContent('Synthetic note')
    await waitFor(() => expect(inboxHeading()).toHaveFocus())
    expect(inboxHeading()).toHaveTextContent('Open alerts (1)')
    expect(screen.getByText('Resolved since this page opened (1)')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(NOTE)
  })

  it.each([
    [new ApiError(409, 'conflict'), 'This alert has already been dealt with. Reload the page to see its current state.'],
    [new ApiError(400, 'note'), 'The note was not accepted. Check it and try again.'],
    [new ApiError(0, 'network_error'), 'Could not resolve the alert. Check your connection and try again.'],
  ])('on %s: the error notice, no announcement, the alert stays, and focus stays on Resolve', async (error, text) => {
    const user = await renderLoaded()
    api.resolveAlertApi.mockRejectedValue(error)
    await submitResolve(user)

    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(text)
    expect(notice).not.toHaveTextContent('Synthetic note')
    expect(announcer()).toBeEmptyDOMElement()
    expect(inboxHeading()).toHaveTextContent('Open alerts (2)')
    expect(screen.queryByText(/Resolved since this page opened/)).not.toBeInTheDocument()
    await waitFor(() => expect(resolveButton()).toHaveFocus())
    expect(inboxHeading()).not.toHaveFocus()

    await user.click(within(notice).getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('after an error, a second try that the API accepts is announced', async () => {
    const user = await renderLoaded()
    api.resolveAlertApi.mockRejectedValueOnce(new ApiError(0, 'network_error'))
    await submitResolve(user)
    await screen.findByRole('alert')
    expect(announcer()).toBeEmptyDOMElement()

    api.resolveAlertApi.mockResolvedValueOnce(ACCEPTED)
    await submitResolve(user)
    await waitFor(() => expect(announcer()).toHaveTextContent(`Resolved: ${A}.`))
    // The new attempt cleared the old error.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(api.resolveAlertApi).toHaveBeenCalledTimes(2)
  })

  it('an empty note sends nothing', async () => {
    const user = await renderLoaded()
    await user.click(resolveButton())
    await user.click(resolveButton())
    expect(screen.getByText('Add a note saying how this alert was dealt with.')).toBeInTheDocument()
    expect(api.resolveAlertApi).not.toHaveBeenCalled()
  })
})
