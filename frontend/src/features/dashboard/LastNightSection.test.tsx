/**
 * The doctor dashboard's "Last night" section (docs/features/nightly-overview.md,
 * criteria 17-25 and 28). `getNightlyOverview` is mocked at the `src/lib/api.ts`
 * boundary; the dashboard data comes from the demo loader (Test Resident A = res-0001, ...).
 */
import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { dashboardLoader, demoDataFor, resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { testFlagged, testOverview } from '../../test/nightlyFixtures'
import { renderWithAuth } from '../../test/render'
import type { AuthUser } from '../../types/auth'
import type { NightlyOverviewResponse } from '../../types/nightly'
import { DoctorDashboard } from './DoctorDashboard'
import { MainPage } from './MainPage'
import { NurseDashboard } from './NurseDashboard'
import { POLL_MS, type DashboardView } from './useDashboardData'

const api = vi.hoisted(() => ({
  getNightlyOverview: vi.fn<(options?: { fresh?: boolean }) => Promise<NightlyOverviewResponse>>(),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, getNightlyOverview: api.getNightlyOverview }
})

vi.mock('./useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./useDashboardData')>()
  const { dashboardLoader } = await import('../../test/dashboardMock')
  return {
    ...actual,
    useDashboardData: (view: DashboardView) => actual.useDashboardData(view, dashboardLoader),
  }
})

import { ApiError } from '../../lib/api'

const HEADING = 'Last night · 18 Nov 2026'

beforeEach(() => {
  resetDashboardLoader()
  api.getNightlyOverview.mockReset()
  api.getNightlyOverview.mockResolvedValue(testOverview())
})

function renderDoctor(user: AuthUser = TEST_USERS.doctor) {
  const ue = userEvent.setup()
  const view = renderWithAuth(<DoctorDashboard />, { user })
  return { user: ue, ...view }
}

async function renderLoaded() {
  const ctx = renderDoctor()
  await screen.findByRole('heading', { level: 2, name: 'Residents' })
  return ctx
}

const section = () => screen.getByRole('region', { name: /^Last night/ })
const rows = () => within(within(section()).getByRole('list')).getAllByRole('listitem')

describe('placement and who sees it (criteria 17, 18)', () => {
  it('sits after the alert summary and before the residents panel', async () => {
    await renderLoaded()
    const heading = await screen.findByRole('heading', { level: 2, name: HEADING })
    const alerts = screen.getByRole('region', { name: /^Open alerts/ })
    const residents = screen.getByRole('heading', { level: 2, name: 'Residents' })
    expect(alerts.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(heading.compareDocumentPosition(residents) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('loads once when the dashboard opens, from the tab copy (not fresh)', async () => {
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    expect(api.getNightlyOverview).toHaveBeenCalledTimes(1)
    expect(api.getNightlyOverview.mock.calls[0][0]).toEqual({ fresh: false })
  })

  it('the nurse dashboard neither shows it nor calls the route', async () => {
    renderWithAuth(<NurseDashboard />, { user: TEST_USERS.nurse })
    await screen.findByRole('heading', { level: 2, name: /^Open alerts/ })
    expect(screen.queryByRole('heading', { name: /^Last night/ })).not.toBeInTheDocument()
    expect(api.getNightlyOverview).not.toHaveBeenCalled()
  })

  it.each([
    ['nurse', ['nurse']],
    ['doctor and nurse (gets the nurse view)', ['doctor', 'nurse']],
    ['caregiver', ['caregiver']],
    ['ops', ['ops']],
    ['family (no access)', ['family']],
  ])('a %s user on / gets no section and no call', async (_label, groups) => {
    renderWithAuth(<MainPage />, { user: { ...TEST_USERS.nurse, groups } })
    // Let the view's loads start and settle.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(screen.queryByRole('heading', { name: /^Last night/ })).not.toBeInTheDocument()
    expect(api.getNightlyOverview).not.toHaveBeenCalled()
  })

  it('a doctor on / gets the section', async () => {
    renderWithAuth(<MainPage />, { user: TEST_USERS.doctor })
    expect(await screen.findByRole('heading', { level: 2, name: HEADING })).toBeInTheDocument()
    expect(api.getNightlyOverview).toHaveBeenCalledTimes(1)
  })
})

describe('flagged list (criteria 19-21)', () => {
  it('shows the heading with expected_date', async () => {
    await renderLoaded()
    expect(await screen.findByRole('heading', { level: 2, name: HEADING })).toBeInTheDocument()
  })

  it("keeps the API's order, even when it isn't alphabetical", async () => {
    api.getNightlyOverview.mockResolvedValue(
      testOverview({
        flagged: [
          testFlagged({ resident_id: 'res-0005', falls: 1 }),
          testFlagged({ resident_id: 'res-0001', vitals_alert_episodes: 2 }),
          testFlagged({ resident_id: 'res-0003', hr_bpm_above: 4, out_of_limit: 4 }),
        ],
      }),
    )
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    expect(within(section()).getByRole('list').tagName).toBe('OL')
    expect(rows().map((r) => within(r).getByRole('link').textContent)).toEqual([
      'Test Resident E',
      'Test Resident A',
      'Test Resident C',
    ])
  })

  it('shows the name linked to the resident page, then the reasons in one line', async () => {
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const [first, second] = rows()
    expect(within(first).getByRole('link', { name: 'Test Resident B' })).toHaveAttribute('href', '/residents/res-0002')
    expect(first).toHaveTextContent(/^Test Resident B 1 fall$/)
    expect(within(second).getByRole('link', { name: 'Test Resident C' })).toHaveAttribute('href', '/residents/res-0003')
    expect(second).toHaveTextContent(/^Test Resident C 1 alert episode · 12 HR above 120 · 3 SpO2 below 92$/)
    // The reasons are one inline element (one phrase for screen readers).
    expect(within(second).getByText('1 alert episode · 12 HR above 120 · 3 SpO2 below 92')).toBeInTheDocument()
  })

  it('still lists a night whose limits could not be read, saying "limit"', async () => {
    api.getNightlyOverview.mockResolvedValue(
      testOverview({
        flagged: [testFlagged({ resident_id: 'res-0002', falls: 1, hr_bpm_above: 12, out_of_limit: 12, thresholds: null })],
      }),
    )
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const [only] = rows()
    expect(only).toHaveTextContent(/^Test Resident B 1 fall · 12 HR above limit$/)
  })

  it("uses each night's own limits and plurals", async () => {
    api.getNightlyOverview.mockResolvedValue(
      testOverview({
        flagged: [
          testFlagged({
            resident_id: 'res-0002',
            falls: 2,
            vitals_alert_episodes: 3,
            hr_bpm_below: 3,
            skin_temp_c_above: 5,
            skin_temp_c_below: 1,
            out_of_limit: 9,
            thresholds: { hr_bpm: { min: 45, max: 110 }, spo2_pct: { min: 88 }, skin_temp_c: { min: 29.5, max: 37.5 } },
          }),
          testFlagged({ resident_id: 'res-0003', spo2_pct_below: 2, out_of_limit: 2 }),
        ],
      }),
    )
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const [first, second] = rows()
    expect(first).toHaveTextContent(
      '2 falls · 3 alert episodes · 3 HR below 45 · 5 skin temp above 37.5 · 1 skin temp below 29.5',
    )
    expect(second).toHaveTextContent(/2 SpO2 below 92$/)
  })

  it("falls back to the resident_id when the resident isn't in the dashboard data", async () => {
    api.getNightlyOverview.mockResolvedValue(
      testOverview({ flagged: [testFlagged({ resident_id: 'res-0099', falls: 1 })] }),
    )
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const link = within(rows()[0]).getByRole('link', { name: 'res-0099' })
    expect(link).toHaveAttribute('href', '/residents/res-0099')
  })

  it('follows the link to the resident page', async () => {
    const { user } = await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    await user.click(within(section()).getByRole('link', { name: 'Test Resident B' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/residents/res-0002'))
  })

  it('reaches the links with the keyboard', async () => {
    const { user } = await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const link = within(section()).getByRole('link', { name: 'Test Resident B' })
    link.focus()
    await waitFor(() => expect(link).toHaveFocus())
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/residents/res-0002'))
  })

  it('marks each row with a warning icon as well as text', async () => {
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    for (const row of rows()) {
      const icon = row.querySelector('svg')
      expect(icon).not.toBeNull()
      expect(icon).toHaveAttribute('aria-hidden', 'true')
      expect(row.textContent?.length).toBeGreaterThan(0)
    }
  })

  it('shows the count lines under the list', async () => {
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const s = within(section())
    expect(s.getByText('14 residents had nothing flagged.')).toBeInTheDocument()
    expect(s.getByText('2 residents have no summary for last night.')).toBeInTheDocument()
    const list = s.getByRole('list')
    expect(list.compareDocumentPosition(s.getByText('14 residents had nothing flagged.')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('uses the singular count lines for 1', async () => {
    api.getNightlyOverview.mockResolvedValue(testOverview({ clear_count: 1, no_summary_count: 1 }))
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const s = within(section())
    expect(s.getByText('1 resident had nothing flagged.')).toBeInTheDocument()
    expect(s.getByText('1 resident has no summary for last night.')).toBeInTheDocument()
  })

  it('leaves out a count line whose count is 0', async () => {
    api.getNightlyOverview.mockResolvedValue(testOverview({ clear_count: 0, no_summary_count: 3 }))
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    expect(section()).not.toHaveTextContent('nothing flagged')
    expect(within(section()).getByText('3 residents have no summary for last night.')).toBeInTheDocument()
  })

  it('has no axe violations', async () => {
    const { container } = await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('nothing flagged (criterion 22)', () => {
  it('says so, then the count lines', async () => {
    api.getNightlyOverview.mockResolvedValue(testOverview({ flagged: [], clear_count: 16, no_summary_count: 0 }))
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const s = within(section())
    expect(s.getByText('Nothing flagged last night.')).toBeInTheDocument()
    expect(s.getByText('16 residents had nothing flagged.')).toBeInTheDocument()
    expect(s.queryByText(/no summary/)).not.toBeInTheDocument()
    expect(s.queryByRole('list')).not.toBeInTheDocument()
  })
})

describe('not ready (criterion 23)', () => {
  const notAvailable = (notReady: boolean) =>
    testOverview({ available: false, not_ready: notReady, flagged: [], clear_count: 0, no_summary_count: 0 })

  it('before 09:30: neutral text, not a warning, no list or counts', async () => {
    api.getNightlyOverview.mockResolvedValue(notAvailable(false))
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const s = within(section())
    const note = s.getByText("Last night's summaries usually appear by 09:30.")
    expect(note.closest('[role="status"], [role="alert"]')).toBeNull()
    expect(s.queryByRole('status')).not.toBeInTheDocument()
    expect(s.queryByRole('list')).not.toBeInTheDocument()
    expect(section()).not.toHaveTextContent(/resident/)
    expect(section()).not.toHaveTextContent(/Nothing flagged/)
  })

  it('at 09:30 or later: a warning status with icon and text, no list or counts', async () => {
    api.getNightlyOverview.mockResolvedValue(notAvailable(true))
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    const s = within(section())
    const status = s.getByRole('status')
    expect(status).toHaveTextContent("Last night's summaries aren't ready yet.")
    expect(status.querySelector('svg')).not.toBeNull()
    expect(s.queryByText(/usually appear by 09:30/)).not.toBeInTheDocument()
    expect(s.queryByRole('list')).not.toBeInTheDocument()
    expect(section()).not.toHaveTextContent(/resident/)
  })

  it('ignores stale counts when available is false', async () => {
    api.getNightlyOverview.mockResolvedValue(
      testOverview({ available: false, not_ready: true, flagged: [], clear_count: 3, no_summary_count: 2 }),
    )
    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    expect(section()).not.toHaveTextContent(/resident/)
  })

  it('has no axe violations', async () => {
    api.getNightlyOverview.mockResolvedValue(notAvailable(true))
    const { container } = await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('loading and error (criterion 24)', () => {
  it('shows a skeleton with an accessible loading status inside the section', async () => {
    const pending = deferred<NightlyOverviewResponse>()
    api.getNightlyOverview.mockReturnValue(pending.promise)
    await renderLoaded()
    const s = within(section())
    expect(s.getByRole('heading', { level: 2, name: 'Last night' })).toBeInTheDocument()
    expect(s.getByRole('status')).toHaveTextContent("Loading last night's overview")
    expect(section().querySelector('[aria-busy="true"]')).not.toBeNull()

    await act(async () => pending.resolve(testOverview()))
    expect(await screen.findByRole('heading', { level: 2, name: HEADING })).toBeInTheDocument()
    expect(within(section()).queryByText("Loading last night's overview")).not.toBeInTheDocument()
  })

  it.each([
    ['a 500', new ApiError(500, 'internal_error')],
    ['throttling (429)', new ApiError(429, 'too_many_requests')],
    ['a network error', new ApiError(0, 'network_error')],
  ])('on %s shows the error and leaves the other sections alone', async (_label, error) => {
    api.getNightlyOverview.mockRejectedValue(error)
    await renderLoaded()
    const alert = await within(section()).findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load last night's overview.")
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    // The rest of the dashboard is still there.
    expect(screen.getByRole('region', { name: /^Open alerts/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Residents' })).toBeInTheDocument()
    expect(screen.queryByText("We couldn't load the dashboard.")).not.toBeInTheDocument()
  })

  it('"Try again" reloads only this section, skipping the tab copy', async () => {
    api.getNightlyOverview.mockRejectedValueOnce(new ApiError(429, 'too_many_requests'))
    const { user } = await renderLoaded()
    const retry = await within(section()).findByRole('button', { name: 'Try again' })
    const dashboardCalls = dashboardLoader.mock.calls.length

    const pending = deferred<NightlyOverviewResponse>()
    api.getNightlyOverview.mockReturnValueOnce(pending.promise)
    await user.click(retry)
    expect(await within(section()).findByRole('status')).toHaveTextContent("Loading last night's overview")
    await act(async () => pending.resolve(testOverview()))

    expect(await screen.findByRole('heading', { level: 2, name: HEADING })).toBeInTheDocument()
    expect(rows()).toHaveLength(2)
    expect(api.getNightlyOverview).toHaveBeenCalledTimes(2)
    expect(api.getNightlyOverview.mock.calls[1][0]).toEqual({ fresh: true })
    expect(dashboardLoader.mock.calls.length).toBe(dashboardCalls)
  })

  it('"Try again" works from the keyboard', async () => {
    api.getNightlyOverview.mockRejectedValueOnce(new ApiError(500, 'internal_error'))
    const { user } = await renderLoaded()
    const retry = await within(section()).findByRole('button', { name: 'Try again' })
    retry.focus()
    await waitFor(() => expect(retry).toHaveFocus())
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { level: 2, name: HEADING })).toBeInTheDocument()
    expect(api.getNightlyOverview).toHaveBeenCalledTimes(2)
  })

  it('has no axe violations in the error state', async () => {
    api.getNightlyOverview.mockRejectedValue(new ApiError(500, 'internal_error'))
    const { container } = await renderLoaded()
    await within(section()).findByRole('alert')
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('reuse (criterion 18)', () => {
  it('asks for the tab copy again (not fresh) when the doctor comes back to the dashboard', async () => {
    const first = await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    first.unmount()
    cleanup()

    await renderLoaded()
    await screen.findByRole('heading', { level: 2, name: HEADING })
    expect(api.getNightlyOverview).toHaveBeenCalledTimes(2)
    expect(api.getNightlyOverview.mock.calls.map((c) => c[0])).toEqual([{ fresh: false }, { fresh: false }])
  })
})

describe('polling (criterion 18, fake clock)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-19T02:00:00Z'))
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("isn't reloaded by the dashboard's 30 s polling", async () => {
    dashboardLoader.mockImplementation(async (view) => demoDataFor(view, new Date()))
    renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByRole('heading', { level: 2, name: HEADING })).toBeInTheDocument()
    const before = dashboardLoader.mock.calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 3)
    })
    expect(dashboardLoader.mock.calls.length).toBeGreaterThanOrEqual(before + 3)
    expect(api.getNightlyOverview).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('heading', { level: 2, name: HEADING })).toBeInTheDocument()
  })

  it("keeps the section's error through polling", async () => {
    api.getNightlyOverview.mockRejectedValue(new ApiError(500, 'internal_error'))
    renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(within(section()).getByRole('alert')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 2)
    })
    expect(api.getNightlyOverview).toHaveBeenCalledTimes(1)
    expect(within(section()).getByRole('alert')).toBeInTheDocument()
  })
})
