/**
 * "Nightly summary" section of the resident page (docs/features/nightly-results.md,
 * criteria 24-30 and 34). `getNightly` is mocked at the `src/lib/api.ts` boundary.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { testNight, testNightly } from '../../test/nightlyFixtures'
import { renderWithAuth } from '../../test/render'
import { resetResidentDetailLoader } from '../../test/residentMock'
import type { AuthUser } from '../../types/auth'
import type { Night, NightlyResponse } from '../../types/nightly'
import { NightlySummary } from './NightlySummary'
import { ResidentDetailPage } from './ResidentDetailPage'

const api = vi.hoisted(() => ({ getNightly: vi.fn<(residentId: string) => Promise<NightlyResponse>>() }))

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, getNightly: api.getNightly }
})

vi.mock('../dashboard/useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../dashboard/useDashboardData')>()
  const { residentDetailLoader } = await import('../../test/residentMock')
  return {
    ...actual,
    useResidentDetail: (id: string) => actual.useResidentDetail(id, residentDetailLoader),
  }
})

beforeEach(() => {
  api.getNightly.mockReset()
  api.getNightly.mockResolvedValue(testNightly())
  resetResidentDetailLoader()
})

// --- helpers -------------------------------------------------------------------

const section = () => screen.getByRole('region', { name: 'Nightly summary' })
const picker = () => within(section()).getByRole('combobox', { name: 'Night' })
const optionLabels = () =>
  within(picker())
    .getAllByRole('option')
    .map((o) => o.textContent)

/** The `<dd>`s that follow a vital's `<dt>`: [statistics, flags?]. */
function vitalRow(label: string): HTMLElement {
  return within(section()).getByText(label, { selector: 'dt' }).parentElement as HTMLElement
}

function renderSection(residentId = 'res-0003') {
  const user = userEvent.setup()
  const view = render(<NightlySummary residentId={residentId} />)
  return { user, ...view }
}

async function renderLoaded(response: NightlyResponse = testNightly()) {
  api.getNightly.mockResolvedValue(response)
  const ctx = renderSection(response.resident_id)
  await screen.findByRole('combobox', { name: 'Night' })
  return ctx
}

const night = (date: string, overrides: Partial<Night> = {}) => testNight({ date, ...overrides })

// --- role-based views (criterion 24, 25) ------------------------------------------

describe('on the resident page', () => {
  function renderPage(user: AuthUser, id = 'res-0003') {
    return renderWithAuth(<ResidentDetailPage />, { user, path: `/residents/${id}`, route: '/residents/:id' })
  }

  it('a doctor sees it between "Latest readings" and "Alert history", loaded once for this resident', async () => {
    renderPage(TEST_USERS.doctor)
    await screen.findByRole('heading', { level: 2, name: /^Alert history/ })
    await screen.findByRole('combobox', { name: 'Night' })

    const h2s = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent ?? '')
    const readings = h2s.indexOf('Latest readings')
    const nightly = h2s.indexOf('Nightly summary')
    const history = h2s.findIndex((t) => t.startsWith('Alert history'))
    expect(readings).toBeGreaterThanOrEqual(0)
    expect(nightly).toBe(readings + 1)
    expect(history).toBe(nightly + 1)

    expect(api.getNightly).toHaveBeenCalledTimes(1)
    expect(api.getNightly).toHaveBeenCalledWith('res-0003')
  })

  it('does not poll: still one call after the page has re-rendered for a while', async () => {
    renderPage(TEST_USERS.doctor)
    await screen.findByRole('combobox', { name: 'Night' })
    // The page's clock ticks every second and re-renders the page.
    await new Promise((resolve) => setTimeout(resolve, 2_200))
    expect(api.getNightly).toHaveBeenCalledTimes(1)
  })

  const doctorAndNurse: AuthUser = { ...TEST_USERS.doctor, groups: ['doctor', 'nurse'] }
  it.each<[string, AuthUser]>([
    ['nurse', TEST_USERS.nurse],
    ['doctor + nurse (gets the nurse view)', doctorAndNurse],
  ])('a %s neither sees it nor calls the route', async (_, user) => {
    renderPage(user)
    await screen.findByRole('heading', { level: 2, name: /^Alert history/ })
    expect(screen.queryByRole('region', { name: 'Nightly summary' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Nightly summary' })).not.toBeInTheDocument()
    expect(api.getNightly).not.toHaveBeenCalled()
  })

  it.each([
    ['caregiver', TEST_USERS.caregiver],
    ['family (removed group)', TEST_USERS.family],
    ['no group', TEST_USERS.noGroup],
  ] as const)('a %s user does not get it or call the route', async (_, user) => {
    renderPage(user)
    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByRole('region', { name: 'Nightly summary' })).not.toBeInTheDocument()
    expect(api.getNightly).not.toHaveBeenCalled()
  })

  it('a failure here does not affect the rest of the page', async () => {
    api.getNightly.mockRejectedValue(new Error('synthetic failure'))
    renderPage(TEST_USERS.doctor)
    await screen.findByRole('heading', { level: 2, name: /^Alert history/ })
    expect(await screen.findByText("We couldn't load the nightly summaries.")).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Latest readings' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: /^Alert history/ })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Vitals thresholds' })).toBeInTheDocument()
    expect(screen.queryByText("We couldn't load this resident.")).not.toBeInTheDocument()
    // Only this section shows an error.
    expect(screen.getAllByRole('alert')).toHaveLength(1)
  })

  it('the doctor page with the section loaded has no axe violations', async () => {
    const { container } = renderPage(TEST_USERS.doctor)
    await screen.findByRole('heading', { level: 2, name: /^Alert history/ })
    await screen.findByRole('combobox', { name: 'Night' })
    expect(await axeViolations(container)).toEqual([])
  })
})

// --- loading, error, empty (criterion 29) -------------------------------------------

describe('states', () => {
  it('loading: a skeleton with an accessible "Loading nightly summary" status, inside the section', async () => {
    const pending = deferred<NightlyResponse>()
    api.getNightly.mockReturnValue(pending.promise)
    const { container } = renderSection()
    expect(within(section()).getByRole('status')).toHaveTextContent('Loading nightly summary')
    expect(section().querySelector('[aria-busy="true"]')).not.toBeNull()
    expect(within(section()).queryByRole('combobox')).not.toBeInTheDocument()
    expect(await axeViolations(container)).toEqual([])

    pending.resolve(testNightly())
    expect(await screen.findByRole('combobox', { name: 'Night' })).toBeInTheDocument()
    expect(within(section()).queryByText('Loading nightly summary')).not.toBeInTheDocument()
  })

  it('error: message and "Try again", which reloads only this section and then shows the night', async () => {
    api.getNightly.mockRejectedValueOnce(new Error('synthetic failure'))
    const { user, container } = renderSection()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load the nightly summaries.")
    expect(await axeViolations(container)).toEqual([])
    expect(api.getNightly).toHaveBeenCalledTimes(1)

    const pending = deferred<NightlyResponse>()
    api.getNightly.mockReturnValueOnce(pending.promise)
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Loading nightly summary')).toBeInTheDocument()
    expect(api.getNightly).toHaveBeenCalledTimes(2)
    expect(api.getNightly).toHaveBeenLastCalledWith('res-0003')

    pending.resolve(testNightly())
    expect(await screen.findByRole('combobox', { name: 'Night' })).toBeInTheDocument()
    expect(within(section()).queryByRole('alert')).not.toBeInTheDocument()
    expect(vitalRow('Heart rate')).toHaveTextContent('min 52 avg 71 max 128')
  })

  it('error again after retry shows the error again', async () => {
    api.getNightly.mockRejectedValue(new Error('synthetic failure'))
    const { user } = renderSection()
    await user.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("We couldn't load the nightly summaries.")
    expect(api.getNightly).toHaveBeenCalledTimes(2)
  })

  it('empty (no nights, not_ready false): the empty message, no picker, no banner', async () => {
    api.getNightly.mockResolvedValue(testNightly({ nights: [] }))
    const { container } = renderSection()
    expect(
      await screen.findByText(
        'No nightly summaries yet. They appear each morning after a night of readings.',
      ),
    ).toBeInTheDocument()
    expect(within(section()).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(section()).queryByText(/isn't ready yet/)).not.toBeInTheDocument()
    expect(within(section()).queryByRole('status')).not.toBeInTheDocument()
    expect(await axeViolations(container)).toEqual([])
  })

  it('a malformed response (rejected by getNightly) shows the error state', async () => {
    api.getNightly.mockRejectedValue(new Error('invalid_response'))
    renderSection()
    expect(await screen.findByRole('alert')).toHaveTextContent("We couldn't load the nightly summaries.")
  })
})

// --- night picker (criterion 26) ---------------------------------------------------------

describe('night picker', () => {
  const three = testNightly({
    // Out of order on purpose: the section sorts newest first.
    nights: [
      night('2026-11-16', { hr_bpm_min: 48, hr_bpm_below: 3, hr_bpm_above: 0 }),
      night('2026-11-18'),
      night('2026-11-17', {
        hr_bpm_above: 0,
        hr_bpm_below: 0,
        thresholds: { hr_bpm: { min: 55, max: 110 }, spo2_pct: { min: 90 }, skin_temp_c: { min: 31, max: 37.5 } },
      }),
    ],
  })

  it('is a select labelled "Night", newest first, formatted like "18 Nov 2026", newest selected', async () => {
    await renderLoaded(three)
    expect(picker().tagName).toBe('SELECT')
    expect(optionLabels()).toEqual(['18 Nov 2026', '17 Nov 2026', '16 Nov 2026'])
    expect(picker()).toHaveValue('2026-11-18')
    expect(within(section()).getByRole('heading', { level: 3, name: '18 Nov 2026' })).toBeInTheDocument()
  })

  it('choosing a night shows that night, with its own limits', async () => {
    const { user } = await renderLoaded(three)
    await user.selectOptions(picker(), '17 Nov 2026')
    expect(picker()).toHaveValue('2026-11-17')
    expect(within(section()).getByRole('heading', { level: 3, name: '17 Nov 2026' })).toBeInTheDocument()
    expect(section()).toHaveTextContent('Limits used that night: HR 55–110 · SpO2 ≥90 · 31–37.5 °C')
    expect(vitalRow('Heart rate')).toHaveTextContent('In range')
    // SpO2 below count 3 against that night's own 90 limit.
    expect(within(vitalRow('SpO2')).getByText('3 below 90')).toBeInTheDocument()

    await user.selectOptions(picker(), '16 Nov 2026')
    expect(within(section()).getByRole('heading', { level: 3, name: '16 Nov 2026' })).toBeInTheDocument()
    expect(vitalRow('Heart rate')).toHaveTextContent('min 48 avg 71 max 128')
    expect(within(vitalRow('Heart rate')).getByText('3 below 50')).toBeInTheDocument()
    expect(within(vitalRow('Heart rate')).queryByText(/above/)).not.toBeInTheDocument()
  })

  it('is keyboard-usable and has a visible focus style', async () => {
    const { user } = await renderLoaded(three)
    await user.tab()
    await waitFor(() => expect(picker()).toHaveFocus())
    expect(picker().className).toMatch(/focus-visible:ring/)
    // jsdom does not change a native select on arrow keys; selectOptions fires the same change.
    await user.selectOptions(picker(), '2026-11-16')
    expect(within(section()).getByRole('heading', { level: 3, name: '16 Nov 2026' })).toBeInTheDocument()
  })

  it('a single night lists just that night', async () => {
    await renderLoaded(testNightly({ nights: [night('2026-11-05')] }))
    expect(optionLabels()).toEqual(['5 Nov 2026'])
  })
})

// --- not ready (criterion 27) ----------------------------------------------------------------

describe('not ready', () => {
  const notReady = testNightly({
    expected_date: '2026-11-19',
    not_ready: true,
    nights: [night('2026-11-17'), night('2026-11-18')],
  })

  it('shows a status with the latest night and a "View" button; figures stay hidden', async () => {
    const { container } = await renderLoaded(notReady)
    const status = within(section()).getByRole('status')
    expect(status).toHaveTextContent("Last night's summary isn't ready yet (latest: 18 Nov 2026)")
    expect(within(status).getByRole('button', { name: 'View 18 Nov 2026' })).toBeInTheDocument()
    expect(within(section()).queryByText('Heart rate')).not.toBeInTheDocument()
    expect(within(section()).queryByText(/Limits used that night/)).not.toBeInTheDocument()
    expect(within(section()).queryByRole('heading', { level: 3 })).not.toBeInTheDocument()
    expect(picker()).toHaveValue('2026-11-18')
    expect(await axeViolations(container)).toEqual([])
  })

  it('pressing "View" shows the latest night, removes the button, and keeps focus in the section', async () => {
    const { user } = await renderLoaded(notReady)
    await user.click(within(section()).getByRole('button', { name: 'View 18 Nov 2026' }))
    expect(within(section()).getByRole('heading', { level: 3, name: '18 Nov 2026' })).toBeInTheDocument()
    expect(vitalRow('Heart rate')).toHaveTextContent('min 52 avg 71 max 128')
    expect(within(section()).queryByRole('button', { name: /^View/ })).not.toBeInTheDocument()
    // The banner itself stays: last night's summary is still missing.
    expect(within(section()).getByRole('status')).toHaveTextContent("isn't ready yet (latest: 18 Nov 2026)")
    await waitFor(() => expect(picker()).toHaveFocus())
  })

  it('the "View" button works from the keyboard', async () => {
    const { user } = await renderLoaded(notReady)
    const button = within(section()).getByRole('button', { name: 'View 18 Nov 2026' })
    button.focus()
    await user.keyboard('{Enter}')
    expect(within(section()).getByRole('heading', { level: 3, name: '18 Nov 2026' })).toBeInTheDocument()
    expect(button.className).toMatch(/focus-visible:ring/)
  })

  it('picking a night also reveals the figures (for that night)', async () => {
    const { user } = await renderLoaded(notReady)
    await user.selectOptions(picker(), '17 Nov 2026')
    expect(within(section()).getByRole('heading', { level: 3, name: '17 Nov 2026' })).toBeInTheDocument()
    expect(within(section()).queryByRole('button', { name: /^View/ })).not.toBeInTheDocument()
  })

  it('with no nights: "Last night\'s summary isn\'t ready yet." and no button, no picker', async () => {
    api.getNightly.mockResolvedValue(testNightly({ not_ready: true, nights: [] }))
    renderSection()
    await screen.findByText("Last night's summary isn't ready yet.")
    const status = within(section()).getByRole('status')
    expect(status).toHaveTextContent("Last night's summary isn't ready yet.")
    expect(status).not.toHaveTextContent('latest')
    expect(within(section()).queryByRole('button')).not.toBeInTheDocument()
    expect(within(section()).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(section()).queryByText(/No nightly summaries yet/)).not.toBeInTheDocument()
  })

  it('when not_ready is false there is no banner', async () => {
    await renderLoaded(testNightly())
    expect(within(section()).queryByRole('status')).not.toBeInTheDocument()
    expect(within(section()).queryByText(/isn't ready yet/)).not.toBeInTheDocument()
  })
})

// --- the night's figures (criterion 28) -----------------------------------------------------

describe("the night's figures", () => {
  it('shows the spec example exactly', async () => {
    const { container } = await renderLoaded()
    expect(within(vitalRow('Heart rate')).getByText('min 52 avg 71 max 128')).toBeInTheDocument()
    expect(within(vitalRow('SpO2')).getByText('min 90 avg 96 max 99')).toBeInTheDocument()
    expect(within(vitalRow('Skin temp')).getByText('min 31.2 avg 33.8 max 35.0')).toBeInTheDocument()

    const s = section()
    expect(within(s).getByText('Activity avg 14%')).toBeInTheDocument()
    expect(within(s).getByText('Readings 1,312 of 1,440 (worn about 22 h)')).toBeInTheDocument()
    expect(within(s).getByText('Time in bed 7 h 40 m')).toBeInTheDocument()
    expect(within(s).getByText('Motion events 23')).toBeInTheDocument()
    expect(within(s).getByText('Falls 0')).toBeInTheDocument()
    expect(within(s).getByText('Alert episodes 1')).toBeInTheDocument()
    expect(within(s).getByText('"I\'m OK" cancels 0')).toBeInTheDocument()
    expect(within(s).getByText('Limits used that night: HR 50–120 · SpO2 ≥92 · 30–38 °C')).toBeInTheDocument()
    expect(await axeViolations(container)).toEqual([])
  })

  it('headings are in order: the section h2, then the night h3', async () => {
    await renderLoaded()
    const levels = within(section())
      .getAllByRole('heading')
      .map((h) => Number(h.tagName.slice(1)))
    expect(levels).toEqual([2, 3])
  })

  it('"above" and "below" flags, each as one phrase with a warning icon, against the night\'s limits', async () => {
    await renderLoaded(testNightly({ nights: [testNight({ hr_bpm_above: 12, hr_bpm_below: 3 })] }))
    const hr = vitalRow('Heart rate')
    const above = within(hr).getByText('12 above 120')
    const below = within(hr).getByText('3 below 50')
    for (const flag of [above, below]) {
      // Text plus an icon (the icon is decorative, the text carries the meaning).
      const item = flag.closest('li') as HTMLElement
      expect(item.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    }
    expect(within(hr).queryByText('In range')).not.toBeInTheDocument()
  })

  it('large counts use thousands separators in flags', async () => {
    await renderLoaded(testNightly({ nights: [testNight({ hr_bpm_above: 1203 })] }))
    expect(within(vitalRow('Heart rate')).getByText('1,203 above 120')).toBeInTheDocument()
  })

  it('SpO2 has only "below"', async () => {
    await renderLoaded()
    const spo2 = vitalRow('SpO2')
    expect(within(spo2).getByText('3 below 92')).toBeInTheDocument()
    expect(spo2).not.toHaveTextContent(/above/)
  })

  it('"In range" (with an icon) when both counts are 0 and there were readings', async () => {
    await renderLoaded(testNightly({ nights: [testNight({ hr_bpm_above: 0, hr_bpm_below: 0, spo2_pct_below: 0 })] }))
    for (const label of ['Heart rate', 'SpO2', 'Skin temp']) {
      const inRange = within(vitalRow(label)).getByText('In range')
      expect(inRange.parentElement?.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(vitalRow(label)).not.toHaveTextContent(/above|below/)
    }
  })

  it('skin temperature flags use one-decimal limits as written', async () => {
    await renderLoaded(
      testNightly({
        nights: [
          testNight({
            skin_temp_c_above: 2,
            skin_temp_c_below: 4,
            thresholds: { hr_bpm: { min: 50, max: 120 }, spo2_pct: { min: 92 }, skin_temp_c: { min: 30.5, max: 37.5 } },
          }),
        ],
      }),
    )
    const skin = vitalRow('Skin temp')
    expect(within(skin).getByText('2 above 37.5')).toBeInTheDocument()
    expect(within(skin).getByText('4 below 30.5')).toBeInTheDocument()
    expect(section()).toHaveTextContent('Limits used that night: HR 50–120 · SpO2 ≥92 · 30.5–37.5 °C')
  })

  it('"No readings" when the statistics are null, and no flag or "In range"', async () => {
    await renderLoaded(
      testNightly({
        nights: [
          testNight({
            hr_bpm_min: null,
            hr_bpm_avg: null,
            hr_bpm_max: null,
            hr_bpm_above: 0,
            hr_bpm_below: 0,
            spo2_pct_min: null,
            spo2_pct_avg: null,
            spo2_pct_max: null,
            spo2_pct_below: 0,
            skin_temp_c_min: null,
            skin_temp_c_avg: null,
            skin_temp_c_max: null,
            activity_pct_avg: null,
            vitals_readings: 0,
          }),
        ],
      }),
    )
    for (const label of ['Heart rate', 'SpO2', 'Skin temp']) {
      const row = vitalRow(label)
      expect(within(row).getByText('No readings')).toBeInTheDocument()
      expect(row).not.toHaveTextContent(/In range|above|below|min /)
    }
    const activity = within(section()).getByText(/^Activity/)
    expect(activity).toHaveTextContent('No readings')
    expect(activity).not.toHaveTextContent('%')
    expect(within(section()).getByText('Readings 0 of 1,440 (worn about 0 h)')).toBeInTheDocument()
  })

  it('"No bed data" when bed_minutes is null', async () => {
    await renderLoaded(testNightly({ nights: [testNight({ bed_minutes: null })] }))
    expect(within(section()).getByText('No bed data')).toBeInTheDocument()
    expect(within(section()).queryByText(/Time in bed/)).not.toBeInTheDocument()
  })

  it.each([
    [1312, 'Readings 1,312 of 1,440 (worn about 22 h)'],
    [1290, 'Readings 1,290 of 1,440 (worn about 22 h)'], // 21.5 rounds up
    [1289, 'Readings 1,289 of 1,440 (worn about 21 h)'],
    [29, 'Readings 29 of 1,440 (worn about 0 h)'],
    [30, 'Readings 30 of 1,440 (worn about 1 h)'],
    [1440, 'Readings 1,440 of 1,440 (worn about 24 h)'],
  ])('worn hours: %i readings -> "%s"', async (readings, text) => {
    await renderLoaded(testNightly({ nights: [testNight({ vitals_readings: readings })] }))
    expect(within(section()).getByText(text)).toBeInTheDocument()
  })

  it.each([
    [0, 'Time in bed 0 h 0 m'],
    [59, 'Time in bed 0 h 59 m'],
    [60, 'Time in bed 1 h 0 m'],
    [460, 'Time in bed 7 h 40 m'],
  ])('bed time: %i minutes -> "%s"', async (minutes, text) => {
    await renderLoaded(testNightly({ nights: [testNight({ bed_minutes: minutes })] }))
    expect(within(section()).getByText(text)).toBeInTheDocument()
  })
})
