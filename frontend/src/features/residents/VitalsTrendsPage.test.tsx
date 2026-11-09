/**
 * Vitals trends page (docs/features/vitals-trends.md, criteria 16-21). `getVitals` and
 * `getResident` are mocked at the `src/lib/api.ts` boundary.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiError } from '../../lib/api'
import { TEST_USERS } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import type { AuthUser } from '../../types/auth'
import type { VitalsResponse, VitalsRow } from '../../types/vitals'
import { VitalsTrendsPage } from './VitalsTrendsPage'

const api = vi.hoisted(() => ({
  getVitals: vi.fn<(id: string, interval: string, from: string, to: string) => Promise<VitalsResponse>>(),
  getResident: vi.fn(),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, getVitals: api.getVitals, getResident: api.getResident }
})

// jsdom has no ResizeObserver, which Recharts' ResponsiveContainer needs.
class NoResize {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', NoResize)

function row(date: string, overrides: Partial<VitalsRow> = {}): VitalsRow {
  return {
    date,
    has_data: true,
    hr_bpm_min: 58,
    hr_bpm_avg: 71.4,
    hr_bpm_max: 104,
    spo2_pct_min: 90,
    spo2_pct_avg: 95.8,
    spo2_pct_max: 99,
    skin_temp_c_min: 32.1,
    skin_temp_c_avg: 33.6,
    skin_temp_c_max: 34.9,
    activity_pct_avg: 14.2,
    vitals_readings: 1302,
    hr_bpm_below: 0,
    hr_bpm_above: 2,
    spo2_pct_below: 0,
    skin_temp_c_below: 0,
    skin_temp_c_above: 0,
    bed_minutes: 512,
    motion_events: 37,
    falls: 1,
    vitals_alert_episodes: 0,
    cancels: 0,
    thresholds: { hr_bpm: { min: 50, max: 120 }, spo2_pct: { min: 88 }, skin_temp_c: { min: 30, max: 38 } },
    ...overrides,
  }
}

function response(rows: VitalsRow[]): VitalsResponse {
  return { resident_id: 'res-0003', interval: 'day', from: '2026-11-17', to: '2026-11-18', thresholds: null, rows }
}

beforeEach(() => {
  api.getVitals.mockReset()
  api.getResident.mockReset()
  api.getVitals.mockResolvedValue(response([row('2026-11-17'), row('2026-11-18')]))
  api.getResident.mockResolvedValue({ resident: { resident_id: 'res-0003', display_name: 'Test Resident C' } })
})

function renderPage(user: AuthUser = TEST_USERS.doctor) {
  const ue = userEvent.setup()
  const view = renderWithAuth(<VitalsTrendsPage />, {
    user,
    path: '/residents/res-0003/vitals',
    route: '/residents/:id/vitals',
  })
  return { user: ue, ...view }
}

it('loads the last 30 days daily by default and shows the table', async () => {
  renderPage()
  expect(screen.getByText('This can take a few seconds.')).toBeInTheDocument()
  const table = await screen.findByRole('table')
  expect(api.getVitals).toHaveBeenCalledTimes(1)
  const [id, interval, from, to] = api.getVitals.mock.calls[0]
  expect([id, interval]).toEqual(['res-0003', 'day'])
  expect(Date.parse(to) - Date.parse(from)).toBe(29 * 86_400_000)
  expect(within(table).getAllByRole('row')).toHaveLength(3)
  expect(await screen.findByRole('heading', { name: 'Test Resident C' })).toBeInTheDocument()
  expect(document.title).not.toContain('Test Resident C')
})

it('summarises the events in text for screen readers', async () => {
  renderPage()
  expect(await screen.findByText(/2 falls, 0 vitals alert episodes, 0 "I'm OK" cancels, 4 readings outside/)).toBeInTheDocument()
})

it('switching to hourly asks for yesterday per hour', async () => {
  const { user } = renderPage()
  await screen.findByRole('table')
  await user.click(screen.getByRole('button', { name: 'Hourly' }))
  await waitFor(() => expect(api.getVitals).toHaveBeenCalledTimes(2))
  const [, interval, from, to] = api.getVitals.mock.calls[1]
  expect(interval).toBe('hour')
  expect(from).toBe(to)
})

it('a range the API would refuse is explained and not requested', async () => {
  const { user } = renderPage()
  await screen.findByRole('table')
  await user.click(screen.getByRole('button', { name: 'Hourly' }))
  await waitFor(() => expect(api.getVitals).toHaveBeenCalledTimes(2))
  await user.click(screen.getByRole('button', { name: 'Last 7 days' }))
  await waitFor(() => expect(api.getVitals).toHaveBeenCalledTimes(3))
  const from = screen.getByLabelText('From')
  await user.clear(from)
  await user.type(from, '2020-01-01')
  expect(await screen.findByText(/Choose at most 7 days|Hourly data covers the last 365 days/)).toBeInTheDocument()
  expect(api.getVitals).toHaveBeenCalledTimes(3)
})

it('shows the empty state when no row has data', async () => {
  api.getVitals.mockResolvedValue(response([row('2026-11-18', { has_data: false })]))
  renderPage()
  expect(await screen.findByText('No readings for this resident in this range.')).toBeInTheDocument()
})

it('a timeout asks for a shorter range, and retry reloads', async () => {
  api.getVitals.mockRejectedValueOnce(new ApiError(504, 'query_timeout'))
  const { user } = renderPage()
  expect(await screen.findByText('This took too long. Try a shorter range.')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /try again|retry/i }))
  expect(await screen.findByRole('table')).toBeInTheDocument()
})

it('downloads a CSV with the opaque ID and a health-data warning', async () => {
  const createObjectURL = vi.fn(() => 'blob:test')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const { user } = renderPage()
  await screen.findByRole('table')
  expect(screen.getByText(/contains health data/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Download CSV' }))
  expect(click).toHaveBeenCalledTimes(1)
  const blob = (createObjectURL.mock.calls[0] as unknown as [Blob])[0]
  const text = await blob.text()
  expect(text.startsWith('resident_id,date,has_data')).toBe(true)
  expect(text).toContain('res-0003,2026-11-17,true')
  expect(text).not.toContain('Test Resident C')
  click.mockRestore()
})

const NOTICE_CASES: Array<[string, AuthUser, string]> = [
  ['nurse', TEST_USERS.nurse, 'This page is for doctors.'],
  ['doctor + nurse', { ...TEST_USERS.doctor, groups: ['doctor', 'nurse'] }, 'This page is for doctors.'],
  ['caregiver', TEST_USERS.caregiver, 'This page is for care staff.'],
]

it.each(NOTICE_CASES)('%s sees a notice and nothing loads', async (_label, user, text) => {
  renderPage(user)
  expect(await screen.findByText(text)).toBeInTheDocument()
  expect(api.getVitals).not.toHaveBeenCalled()
  expect(api.getResident).not.toHaveBeenCalled()
})

it('ops sees the no-access screen', async () => {
  renderPage(TEST_USERS.ops)
  await waitFor(() => expect(screen.queryByRole('table')).not.toBeInTheDocument())
  expect(api.getVitals).not.toHaveBeenCalled()
})
