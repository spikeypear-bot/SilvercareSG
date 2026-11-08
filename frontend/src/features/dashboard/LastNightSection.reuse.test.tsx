/**
 * "Last night" reuse across visits (docs/features/nightly-overview.md, criterion 18),
 * through the real `getNightlyOverview` tab copy. `src/lib/auth.ts` is mocked and `fetch`
 * is stubbed, so nothing reaches Cognito or the real API. Only `Date` is faked.
 */
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDashboardLoader } from '../../test/dashboardMock'
import { TEST_USERS } from '../../test/fixtures'
import { testFlagged, testOverview } from '../../test/nightlyFixtures'
import { renderWithAuth } from '../../test/render'
import { DoctorDashboard } from './DoctorDashboard'
import type { DashboardView } from './useDashboardData'

vi.mock('../../lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/auth')>()
  const { authMock } = await import('../../test/authMock')
  return { ...actual, ...authMock }
})

vi.mock('./useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./useDashboardData')>()
  const { dashboardLoader } = await import('../../test/dashboardMock')
  return {
    ...actual,
    useDashboardData: (view: DashboardView) => actual.useDashboardData(view, dashboardLoader),
  }
})

import { clearNightlyOverviewCache } from '../../lib/api'
import { authMock, resetAuthMock } from '../../test/authMock'

const T0 = new Date('2026-11-19T01:25:00Z') // 09:25 SGT
const MINUTE = 60_000
const fetchMock = vi.fn<typeof fetch>()

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

const notReady = testOverview({ available: false, not_ready: false, flagged: [], clear_count: 0, no_summary_count: 0 })
const ready = testOverview({ flagged: [testFlagged({ resident_id: 'res-0002', falls: 1 })], clear_count: 15, no_summary_count: 0 })

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(T0)
  clearNightlyOverviewCache()
  resetDashboardLoader()
  resetAuthMock()
  authMock.getApiToken.mockResolvedValue('test-id-token-overview')
  vi.stubEnv('VITE_API_URL', 'https://api.example.test')
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  fetchMock.mockImplementationOnce(async () => jsonResponse(notReady))
  fetchMock.mockImplementation(async () => jsonResponse(ready))
})

afterEach(() => {
  cleanup()
  clearNightlyOverviewCache()
  vi.useRealTimers()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

const overviewCalls = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/nightly/overview'))

async function visit() {
  const view = renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
  await screen.findByRole('heading', { level: 2, name: 'Last night · 18 Nov 2026' })
  return view
}

describe('reuse across visits', () => {
  it('a doctor who opened at 09:25 sees the neutral note until 10 minutes later, then the results', async () => {
    const first = await visit()
    expect(screen.getByText("Last night's summaries usually appear by 09:30.")).toBeInTheDocument()
    first.unmount()

    vi.setSystemTime(T0.getTime() + 9 * MINUTE)
    const second = await visit()
    expect(screen.getByText("Last night's summaries usually appear by 09:30.")).toBeInTheDocument()
    expect(overviewCalls()).toHaveLength(1)
    second.unmount()

    vi.setSystemTime(T0.getTime() + 10 * MINUTE)
    await visit()
    const section = screen.getByRole('region', { name: /^Last night/ })
    expect(await within(section).findByRole('link', { name: 'Test Resident B' })).toBeInTheDocument()
    expect(overviewCalls()).toHaveLength(2)
  })

  it('"Try again" after an error loads at once, and a later visit reuses that answer', async () => {
    fetchMock.mockReset()
    fetchMock.mockImplementationOnce(async () =>
      new Response(JSON.stringify({ error: { code: 'too_many_requests', message: 'x' } }), { status: 429 }),
    )
    fetchMock.mockImplementation(async () => jsonResponse(ready))
    const user = userEvent.setup()
    const first = renderWithAuth(<DoctorDashboard />, { user: TEST_USERS.doctor })
    const section = () => screen.getByRole('region', { name: /^Last night/ })
    await screen.findByRole('region', { name: /^Last night/ })
    await user.click(await within(section()).findByRole('button', { name: 'Try again' }))
    expect(await within(section()).findByRole('link', { name: 'Test Resident B' })).toBeInTheDocument()
    expect(overviewCalls()).toHaveLength(2)
    first.unmount()

    vi.setSystemTime(T0.getTime() + 5 * MINUTE)
    await visit()
    expect(within(section()).getByRole('link', { name: 'Test Resident B' })).toBeInTheDocument()
    expect(overviewCalls()).toHaveLength(2)
  })
})
