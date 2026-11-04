import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { renderWithAuth } from '../../test/render'
import { demoDetail, residentDetailLoader, resetResidentDetailLoader } from '../../test/residentMock'
import type { AuthUser } from '../../types/auth'
import type { ResidentAlert } from '../../types/dashboard'
import type { ResidentDetail } from '../../types/resident'
import { ResidentDetailPage } from './ResidentDetailPage'

vi.mock('../dashboard/useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../dashboard/useDashboardData')>()
  const { residentDetailLoader } = await import('../../test/residentMock')
  return {
    ...actual,
    useResidentDetail: (id: string) => actual.useResidentDetail(id, residentDetailLoader),
  }
})

beforeEach(() => {
  resetResidentDetailLoader()
})

const NURSE_D: AuthUser = { name: 'Test Nurse D', email: 'test.nurse.d@example.test', groups: ['nurse'] }
const DOCTOR_C: AuthUser = { name: 'Test Doctor C', email: 'test.doctor.c@example.test', groups: ['doctor'] }

function renderPage(id: string, user: AuthUser = TEST_USERS.nurse) {
  const ue = userEvent.setup()
  const view = renderWithAuth(<ResidentDetailPage />, {
    user,
    path: `/residents/${id}`,
    route: '/residents/:id',
  })
  return { user: ue, ...view }
}

async function renderLoaded(id: string, user: AuthUser = TEST_USERS.nurse, detail?: ResidentDetail) {
  if (detail) residentDetailLoader.mockResolvedValue(detail)
  const ctx = renderPage(id, user)
  await screen.findByRole('heading', { level: 2, name: /^Alert history/ })
  return ctx
}

// --- queries ---------------------------------------------------------------

const region = (name: string | RegExp) => screen.getByRole('region', { name })
const devices = () => region('Devices')
const readings = () => region('Latest readings')
const history = () => region(/^Alert history/)
const thresholds = () => region('Vitals thresholds')
const changeHistory = () => region('Change history')
const historyEntries = () => {
  const list = changeHistory().querySelector('ol')
  return list ? (Array.from(list.children) as HTMLElement[]) : []
}
const announcer = () => screen.getByRole('status')
const location = () => screen.getByTestId('location').textContent

const deviceBox = (name: RegExp) =>
  within(devices()).getByRole('heading', { level: 3, name }).parentElement as HTMLElement
const fact = (box: HTMLElement, label: string) =>
  within(box).getByText(label, { selector: 'dt' }).nextElementSibling as HTMLElement
const limit = (label: string) =>
  within(thresholds()).getByText(label, { selector: 'dt' }).nextElementSibling as HTMLElement

const table = (name: RegExp) => within(readings()).getByRole('table', { name })
const bodyRows = (t: HTMLElement) => within(t).getAllByRole('row').slice(1)
const cellTexts = (t: HTMLElement) =>
  bodyRows(t).map((row) => within(row).getAllByRole('cell').map((c) => c.textContent))
const rowTimes = (t: HTMLElement) =>
  bodyRows(t).map((row) => within(row).getByRole('rowheader').querySelector('time')?.getAttribute('datetime'))

const historyRows = () => {
  const list = history().querySelector('ul')
  return list ? within(list as HTMLElement).getAllByRole('listitem') : []
}

const headerCard = (name: string) =>
  screen.getByRole('heading', { level: 2, name }).closest('.rounded-2xl') as HTMLElement

// --- route and roles ---------------------------------------------------------

describe('roles', () => {
  it.each([
    ['nurse', TEST_USERS.nurse],
    ['doctor', TEST_USERS.doctor],
  ] as const)('a %s sees the resident page', async (_, user) => {
    renderPage('res-0001', user)
    expect(screen.getByRole('heading', { level: 1, name: 'Resident details' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 2, name: 'Test Resident A' })).toBeInTheDocument()
    expect(residentDetailLoader).toHaveBeenCalledWith('res-0001')
  })

  it('a caregiver gets the staff-only notice and nothing is loaded', async () => {
    const { user: ue } = renderPage('res-0002', TEST_USERS.caregiver)
    expect(screen.getByRole('heading', { level: 1, name: 'Staff only' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'This page is for care staff.' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: "Your account isn't set up yet" })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Resident details' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Back to dashboard' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Test Resident/)).not.toBeInTheDocument()
    expect(residentDetailLoader).not.toHaveBeenCalled()
    await waitFor(() => expect(document.title).toBe('SilverCare - Staff only'))

    const back = screen.getByRole('link', { name: 'Back to my dashboard' })
    expect(back).toHaveAttribute('href', '/')
    await ue.click(back)
    expect(await screen.findByText('Dashboard screen')).toBeInTheDocument()
    expect(location()).toBe('/')
    expect(residentDetailLoader).not.toHaveBeenCalled()
  })

  it('a user only in the removed "family" group gets the no-access page, not the staff-only notice', async () => {
    renderPage('res-0002', TEST_USERS.family)
    expect(screen.getByRole('heading', { level: 1, name: "Your account isn't set up yet" })).toBeInTheDocument()
    expect(screen.queryByText('This page is for care staff.')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Resident details' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Test Resident/)).not.toBeInTheDocument()
    expect(residentDetailLoader).not.toHaveBeenCalled()
    await waitFor(() => expect(document.title).toBe('SilverCare - No access'))
  })

  it('the staff-only notice has no axe violations and its headings are in order', async () => {
    const { container } = renderPage('res-0002', TEST_USERS.caregiver)
    await screen.findByRole('heading', { level: 2, name: 'This page is for care staff.' })
    expect(await axeViolations(container)).toEqual([])
    const levels = screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))
    expect(levels).toEqual([1, 2])
  })

  it('a user with no group gets the no-access page and nothing is loaded', async () => {
    renderPage('res-0002', TEST_USERS.noGroup)
    expect(screen.getByRole('heading', { level: 1, name: "Your account isn't set up yet" })).toBeInTheDocument()
    expect(screen.queryByText('This page is for care staff.')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Resident details' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Test Resident/)).not.toBeInTheDocument()
    expect(residentDetailLoader).not.toHaveBeenCalled()
    await waitFor(() => expect(document.title).toBe('SilverCare - No access'))
  })

  it.each([
    [['nurse', 'doctor']],
    [['doctor', 'nurse']],
  ])('a user in %j gets the nurse view (actions, read-only thresholds)', async (groups) => {
    await renderLoaded('res-0003', { ...TEST_USERS.nurse, groups })
    expect(screen.getByRole('button', { name: 'Acknowledge: Fall detected, Test Resident C' })).toBeInTheDocument()
    expect(within(thresholds()).getByText('Only doctors can change thresholds.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit thresholds' })).not.toBeInTheDocument()
  })
})

// --- page states -------------------------------------------------------------

describe('page states', () => {
  it('shows the loading skeleton with a "Loading resident" status, then the page', async () => {
    const pending = deferred<ResidentDetail | null>()
    residentDetailLoader.mockReturnValue(pending.promise)
    const { container } = renderPage('res-0001')
    expect(screen.getByRole('status')).toHaveTextContent('Loading resident')
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
    expect(screen.getByRole('heading', { level: 1, name: 'Resident details' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to dashboard' })).toHaveAttribute('href', '/')
    expect(screen.queryByRole('region', { name: 'Devices' })).not.toBeInTheDocument()

    pending.resolve(demoDetail('res-0001'))
    expect(await screen.findByRole('heading', { level: 2, name: 'Test Resident A' })).toBeInTheDocument()
    expect(screen.queryByText('Loading resident')).not.toBeInTheDocument()
    expect(container.querySelector('[aria-busy="true"]')).toBeNull()
  })

  it('shows "We couldn\'t load this resident." with Try again, which retries', async () => {
    residentDetailLoader.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderPage('res-0001')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load this resident.")
    expect(screen.queryByText('Resident not found')).not.toBeInTheDocument()

    const pending = deferred<ResidentDetail | null>()
    residentDetailLoader.mockReturnValueOnce(pending.promise)
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Loading resident')

    pending.resolve(demoDetail('res-0001'))
    expect(await screen.findByRole('heading', { level: 2, name: 'Test Resident A' })).toBeInTheDocument()
    expect(residentDetailLoader).toHaveBeenCalledTimes(2)
  })

  it('shows "Resident not found" for an unknown id', async () => {
    renderPage('res-9999')
    expect(await screen.findByText('Resident not found')).toBeInTheDocument()
    expect(
      screen.getByText('Check the link, or go back to the dashboard and choose a resident.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Devices' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to dashboard' })).toBeInTheDocument()
  })

  it('moves focus to the Back to dashboard link on open, and the link goes to /', async () => {
    const { user } = await renderLoaded('res-0001')
    const back = screen.getByRole('link', { name: 'Back to dashboard' })
    await waitFor(() => expect(back).toHaveFocus())
    await user.click(back)
    expect(await screen.findByText('Dashboard screen')).toBeInTheDocument()
    expect(location()).toBe('/')
  })
})

// --- header ------------------------------------------------------------------

describe('resident header', () => {
  it('shows name, resident_id, home_id, status badge with icon, status_text, and wearable state', async () => {
    const detail = demoDetail('res-0003')
    await renderLoaded('res-0003', TEST_USERS.nurse, detail)
    const card = headerCard('Test Resident C')
    expect(card).toHaveTextContent('res-0003')
    expect(card).toHaveTextContent(detail.resident.home_id)
    expect(within(card).getByText('Home')).toHaveClass('sr-only')
    const badge = card.querySelector('[data-tone]') as HTMLElement
    expect(badge).toHaveAttribute('data-tone', 'urgent')
    expect(badge).toHaveTextContent('Urgent')
    expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(within(card).getByText('Urgent: check now')).toBeInTheDocument()
    expect(within(card).getByText('Worn')).toBeInTheDocument()
  })

  it.each([
    ['res-0010', 'Test Resident J', 'Not worn · Charging', 'OK'],
    ['res-0015', 'Test Resident O', 'Offline · Worn', 'Attention'],
  ])('%s shows "%s" with "%s"', async (id, name, wearableText, badge) => {
    await renderLoaded(id)
    const card = headerCard(name)
    expect(within(card).getByText(wearableText)).toBeInTheDocument()
    expect(card.querySelector('[data-tone]')).toHaveTextContent(badge)
  })
})

// --- devices (pattern 10) ----------------------------------------------------

describe('devices section (pattern 10)', () => {
  it('shows the wearable and bedroom sensor for res-0001, with a mains-powered bedroom', async () => {
    const detail = demoDetail('res-0001')
    await renderLoaded('res-0001', TEST_USERS.nurse, detail)
    const wearable = detail.wearable!
    const bedroom = detail.bedroom!

    const w = deviceBox(/^Wearable/)
    expect(w.querySelector('h3')).toHaveTextContent(wearable.device_id)
    expect(fact(w, 'Connection')).toHaveTextContent('Online')
    expect(fact(w, 'Connection').querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(fact(w, 'Battery')).toHaveTextContent('82%')
    expect(fact(w, 'State')).toHaveTextContent('Worn')
    expect(fact(w, 'Last seen').querySelector('time')).toHaveAttribute('datetime', wearable.latest.status[0].ts)
    expect(fact(w, 'Firmware')).toHaveTextContent('1.0.0')

    const b = deviceBox(/^Bedroom sensor/)
    expect(b.querySelector('h3')).toHaveTextContent(bedroom.device_id)
    expect(fact(b, 'Connection')).toHaveTextContent('Online')
    expect(fact(b, 'Battery')).toHaveTextContent('Mains powered')
    expect(fact(b, 'Last seen').querySelector('time')).toHaveAttribute('datetime', bedroom.latest.status[0].ts)
    expect(fact(b, 'Firmware')).toHaveTextContent('1.0.0')
  })

  it.each([
    ['res-0009', /^Bedroom sensor/, 'Connection', 'Offline'],
    ['res-0002', /^Bedroom sensor/, 'Battery', /^\d+%$/],
    ['res-0015', /^Wearable/, 'Connection', 'Offline'],
    ['res-0010', /^Wearable/, 'State', 'Not worn · Charging'],
    ['res-0006', /^Wearable/, 'Battery', '15%'],
  ] as const)('%s: %s %s shows %s', async (id, device, label, text) => {
    await renderLoaded(id)
    expect(fact(deviceBox(device), label)).toHaveTextContent(text)
  })

  it.each([
    ['res-0004', /^Bedroom sensor/, /^9%\s?· Low$/],
    ['res-0006', /^Wearable/, /^15%\s?· Low$/],
    ['res-0013', /^Wearable/, /^12%\s?· Low$/],
  ] as const)('%s: %s battery below 20%% shows %s as text', async (id, device, text) => {
    await renderLoaded(id)
    const battery = fact(deviceBox(device), 'Battery')
    expect(battery).toHaveTextContent(text)
    expect(battery.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it.each([
    ['res-0004', /^Wearable/, '45%'],
    ['res-0002', /^Bedroom sensor/, /^\d+%$/],
    ['res-0020', /^Wearable/, '27%'],
  ] as const)('%s: %s battery at or above 20%% has no "Low" marker', async (id, device, text) => {
    await renderLoaded(id)
    const battery = fact(deviceBox(device), 'Battery')
    expect(battery).toHaveTextContent(text)
    expect(battery).not.toHaveTextContent('Low')
  })

  it('does not show the "Staff area" nav (it has its own back link)', async () => {
    await renderLoaded('res-0003')
    expect(screen.queryByRole('navigation', { name: 'Staff area' })).not.toBeInTheDocument()
    expect(document.querySelector('a[href="/devices"]')).toBeNull()
  })

  it('shows empty texts when one device is missing', async () => {
    const detail = demoDetail('res-0001')
    await renderLoaded('res-0001', TEST_USERS.nurse, { ...detail, wearable: null })
    expect(within(devices()).getByText('No wearable assigned.')).toBeInTheDocument()
    expect(within(devices()).getByRole('heading', { level: 3, name: /^Bedroom sensor/ })).toBeInTheDocument()
    expect(within(readings()).queryByRole('heading', { name: /^Wearable/ })).not.toBeInTheDocument()
    expect(within(readings()).queryByRole('table', { name: /^Vitals/ })).not.toBeInTheDocument()
  })

  it('shows "No bedroom sensor assigned." when the bedroom sensor is missing', async () => {
    const detail = demoDetail('res-0001')
    await renderLoaded('res-0001', TEST_USERS.nurse, { ...detail, bedroom: null })
    expect(within(devices()).getByText('No bedroom sensor assigned.')).toBeInTheDocument()
    expect(within(readings()).queryByRole('table', { name: /^Motion/ })).not.toBeInTheDocument()
  })

  it('shows one empty text each when no devices are assigned', async () => {
    const detail = demoDetail('res-0001')
    await renderLoaded('res-0001', TEST_USERS.nurse, { ...detail, wearable: null, bedroom: null })
    expect(within(devices()).getByText('No devices are assigned to this resident.')).toBeInTheDocument()
    expect(within(devices()).queryByRole('listitem')).not.toBeInTheDocument()
    expect(within(readings()).getByText('No devices, so no readings.')).toBeInTheDocument()
  })

  it('shows "No status message yet" for a device with no status messages', async () => {
    const detail = demoDetail('res-0001')
    const wearable = { ...detail.wearable!, latest: { ...detail.wearable!.latest, status: [] } }
    const bedroom = { ...detail.bedroom!, latest: { ...detail.bedroom!.latest, status: [] } }
    await renderLoaded('res-0001', TEST_USERS.nurse, { ...detail, wearable, bedroom })
    for (const name of [/^Wearable/, /^Bedroom sensor/]) {
      const box = deviceBox(name)
      expect(fact(box, 'Last seen')).toHaveTextContent('No status message yet')
      expect(fact(box, 'Connection')).toHaveTextContent('Online')
      expect(within(box).queryByText('Battery')).not.toBeInTheDocument()
    }
  })
})

// --- latest readings (pattern 11) --------------------------------------------

describe('latest readings (pattern 11)', () => {
  it('has a table per message type with at most 5 rows, newest first, matching the data', async () => {
    const detail = demoDetail('res-0003')
    await renderLoaded('res-0003', TEST_USERS.nurse, detail)
    const w = detail.wearable!.latest
    const b = detail.bedroom!.latest
    const cases: Array<[RegExp, Array<{ ts: string }>]> = [
      [/^Vitals \(latest/, w.vitals],
      [/^Falls detected by the wearable \(latest/, w.fall],
      [/^Wearable status \(latest/, w.status],
      [/^Motion \(latest/, b.motion],
      [/^Bed \(latest/, b.bed],
      [/^Bedroom sensor status \(latest/, b.status],
    ]
    for (const [name, rows] of cases) {
      const t = table(name)
      expect(bodyRows(t).length).toBeLessThanOrEqual(5)
      expect(bodyRows(t)).toHaveLength(rows.length)
      expect(t).toHaveAccessibleName(`${t.querySelector('caption')?.textContent}`)
      expect(t.querySelector('caption')).toHaveTextContent(`(latest ${rows.length}, newest first)`)
      const times = rowTimes(t)
      expect(times).toEqual(rows.map((r) => r.ts))
      expect([...times].sort().reverse()).toEqual(times)
    }
    expect(within(table(/^Vitals/)).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Time',
      'Heart rate',
      'SpO2',
      'Skin temp',
      'Activity',
    ])
  })

  it('formats res-0001 values with the metrics.md fields', async () => {
    await renderLoaded('res-0001')
    expect(cellTexts(table(/^Vitals/))).toEqual([
      ['68 bpm', '97%', '33.6 °C', '22%'],
      ['70 bpm', '96%', '33.7 °C', '26%'],
      ['67 bpm', '97%', '33.5 °C', '19%'],
      ['71 bpm', '98%', '33.8 °C', '28%'],
      ['66 bpm', '97%', '33.6 °C', '24%'],
    ])
    expect(cellTexts(table(/^Wearable status/))).toEqual([
      ['82%', 'Yes', 'No'],
      ['83%', 'Yes', 'No'],
      ['84%', 'Yes', 'No'],
      ['85%', 'Yes', 'No'],
      ['86%', 'Yes', 'No'],
    ])
    expect(cellTexts(table(/^Motion/))).toEqual([['Detected'], ['Cleared'], ['Detected'], ['Cleared'], ['Detected']])
    expect(cellTexts(table(/^Bed \(/))).toEqual([
      ['Out of bed'],
      ['In bed'],
      ['Out of bed'],
      ['In bed'],
      ['Out of bed'],
    ])
    expect(cellTexts(table(/^Bedroom sensor status/))).toEqual(Array(5).fill(['Mains powered']))
  })

  it('shows "No reading" for a missing SpO2 value (res-0004)', async () => {
    await renderLoaded('res-0004')
    expect(cellTexts(table(/^Vitals/))[2][1]).toBe('No reading')
  })

  it('shows charging wearable status rows (res-0010)', async () => {
    await renderLoaded('res-0010')
    expect(cellTexts(table(/^Wearable status/))[0]).toEqual(['58%', 'No', 'Yes'])
  })

  it('shows sparklines with sr-only text for heart rate, SpO2, skin temp and activity', async () => {
    await renderLoaded('res-0001')
    const r = readings()
    expect(within(r).getByText('Last 5 readings, oldest first: 66 bpm, 71 bpm, 67 bpm, 70 bpm, 68 bpm.')).toHaveClass(
      'sr-only',
    )
    expect(within(r).getByText('Last 5 readings, oldest first: 97%, 98%, 97%, 96%, 97%.')).toHaveClass('sr-only')
    expect(
      within(r).getByText('Last 5 readings, oldest first: 33.6 °C, 33.8 °C, 33.5 °C, 33.7 °C, 33.6 °C.'),
    ).toHaveClass('sr-only')
    expect(within(r).getByText('Last 5 readings, oldest first: 24%, 28%, 19%, 26%, 22%.')).toHaveClass('sr-only')
    const sparklines = r.querySelectorAll('svg[aria-hidden="true"] polyline')
    expect(sparklines).toHaveLength(4)
    // Latest value per vital, shown above the sparkline.
    const trends = r.querySelector('dl') as HTMLElement
    expect(within(trends).getByText('Heart rate', { selector: 'dt' }).nextElementSibling).toHaveTextContent('68 bpm')
    expect(within(trends).getByText('Activity', { selector: 'dt' }).nextElementSibling).toHaveTextContent('22%')
  })

  it('shows "No falls recorded." for res-0001', async () => {
    await renderLoaded('res-0001')
    expect(within(readings()).queryByRole('table', { name: /^Falls/ })).not.toBeInTheDocument()
    expect(within(readings()).getByText('Falls detected by the wearable')).toBeInTheDocument()
    expect(within(readings()).getByText('No falls recorded.')).toBeInTheDocument()
  })

  it.each(['res-0003', 'res-0017'])('%s shows fall rows', async (id) => {
    const detail = demoDetail(id)
    await renderLoaded(id, TEST_USERS.nurse, detail)
    const rows = cellTexts(table(/^Falls/))
    expect(rows.length).toBeGreaterThan(0)
    expect(rows).toEqual(
      detail.wearable!.latest.fall.map((f) => [
        `${Math.round(f.confidence * 100)}%`,
        `${f.peak_g.toFixed(1)} g`,
        `${f.still_s} s`,
      ]),
    )
    expect(within(readings()).queryByText('No falls recorded.')).not.toBeInTheDocument()
  })

  it('res-0003 fall values', async () => {
    await renderLoaded('res-0003')
    expect(cellTexts(table(/^Falls/))).toEqual([
      ['90%', '4.1 g', '17 s'],
      ['94%', '2.9 g', '22 s'],
    ])
  })

  it('shows empty texts and no sparklines when a device has no messages', async () => {
    const detail = demoDetail('res-0001')
    const wearable = { ...detail.wearable!, latest: { vitals: [], fall: [], status: [] } }
    const bedroom = { ...detail.bedroom!, latest: { motion: [], bed: [], status: [] } }
    await renderLoaded('res-0001', TEST_USERS.nurse, { ...detail, wearable, bedroom })
    const r = readings()
    expect(within(r).queryAllByRole('table')).toEqual([])
    expect(
      within(r).getByText('No vitals readings yet. The wearable sends one every minute while it is worn.'),
    ).toBeInTheDocument()
    expect(within(r).getByText('No motion events yet.')).toBeInTheDocument()
    expect(within(r).getByText('No bed events yet.')).toBeInTheDocument()
    expect(within(r).getAllByText('No status messages yet.')).toHaveLength(2)
    expect(r.querySelector('svg polyline')).toBeNull()
    expect(within(r).queryByText(/^Last \d+ readings/)).not.toBeInTheDocument()
  })
})

// --- alert history (pattern 12) ----------------------------------------------

const LIFECYCLE = { open: 'Open', acknowledged: 'Acknowledged', cancelled: 'Cancelled', resolved: 'Resolved' } as const

function expectRowMatches(row: HTMLElement, alert: ResidentAlert) {
  expect(row.querySelector('time')).toHaveAttribute('datetime', alert.event_ts)
  expect(within(row).getByText(LIFECYCLE[alert.status])).toBeInTheDocument()
  if (alert.acknowledged_at) expect(row).toHaveTextContent('Acknowledged by the care centre nurse,')
  else expect(row).not.toHaveTextContent('Acknowledged by')
  if (alert.acknowledged_at && alert.ack_note) {
    expect(row).toHaveTextContent('Message from the care centre:')
    expect(row).toHaveTextContent(alert.ack_note.replace(/\s+/g, ' '))
  } else {
    expect(row).not.toHaveTextContent('Message from the care centre')
  }
  if (alert.status === 'resolved') expect(row).toHaveTextContent(`Resolved by ${alert.resolved_by},`)
  else expect(row).not.toHaveTextContent('Resolved by')
  if (alert.status === 'cancelled' && alert.cancelled_by_role === 'caregiver') {
    expect(row).toHaveTextContent(`Cancelled by ${alert.cancelled_by ?? 'a caregiver'} (caregiver),`)
  } else if (alert.status === 'cancelled') {
    expect(row).toHaveTextContent('Cancelled by the resident ("I\'m OK"),')
  } else {
    expect(row).not.toHaveTextContent('Cancelled by')
  }
}

describe('alert history (pattern 12)', () => {
  it.each(['res-0003', 'res-0007', 'res-0002', 'res-0015', 'res-0004', 'res-0008'])(
    '%s lists every alert newest first, with lifecycle and by-lines',
    async (id) => {
      const detail = demoDetail(id)
      await renderLoaded(id, TEST_USERS.doctor, detail)
      expect(screen.getByRole('heading', { level: 2, name: `Alert history (${detail.alerts.length})` })).toBeInTheDocument()
      const rows = historyRows()
      expect(rows).toHaveLength(detail.alerts.length)
      const times = rows.map((r) => r.querySelector('time')?.getAttribute('datetime') as string)
      expect([...times].sort().reverse()).toEqual(times)
      rows.forEach((row, i) => expectRowMatches(row, detail.alerts[i]))
      expect(detail.alerts.some((a) => a.status === 'resolved')).toBe(true)
    },
  )

  it('res-0003: Open fall first, then resolved, caregiver-cancelled, and resolved history', async () => {
    await renderLoaded('res-0003', TEST_USERS.doctor)
    const rows = historyRows()
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('Fall detected')
    expect(within(rows[0]).getByText('Open')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Critical')).toBeInTheDocument()

    expect(rows[1]).toHaveTextContent('Possible fall')
    expect(rows[1]).toHaveTextContent('Acknowledged by the care centre nurse,')
    expect(rows[1]).toHaveTextContent('Message from the care centre:')
    expect(rows[1]).toHaveTextContent('Synthetic note: the care centre is calling the resident.')
    expect(rows[1]).toHaveTextContent('Resolved by Test Nurse C')
    expect(within(rows[1]).getByText('Resolved')).toBeInTheDocument()

    // (i + k) = 3: cancelled by a caregiver in the portal, never acknowledged.
    expect(rows[2]).toHaveTextContent('Fall detected')
    expect(within(rows[2]).getByText('Cancelled')).toBeInTheDocument()
    expect(within(rows[2]).getByText(/^Cancelled by/)).toHaveTextContent(/^Cancelled by Test Caregiver B \(caregiver\), /)
    expect(rows[2]).not.toHaveTextContent('Acknowledged by')
    expect(rows[2]).not.toHaveTextContent('Resolved by')

    expect(rows[3]).toHaveTextContent('Resolved by Test Nurse B')
    expect(within(rows[3]).getByText('Resolved')).toBeInTheDocument()
    // No staff name ever appears in an acknowledge line.
    rows.forEach((r) => expect(r).not.toHaveTextContent(/Acknowledged by Test/))
  })

  it('res-0007: an acknowledged unresolved alert shows "Acknowledged by the care centre nurse" and the note', async () => {
    await renderLoaded('res-0007', TEST_USERS.doctor)
    const first = historyRows()[0]
    expect(within(first).getByText('Acknowledged')).toBeInTheDocument()
    expect(first).toHaveTextContent(/Acknowledged by the care centre nurse, 6 min ago/)
    expect(within(first).getByText('Message from the care centre:')).toBeInTheDocument()
    expect(within(first).getByText('Synthetic note: ambulance on the way, 8 minutes.')).toHaveClass('whitespace-pre-line')
  })

  it('a caregiver cancel after an acknowledge shows both lines and the note', async () => {
    const detail = demoDetail('res-0003')
    const [first, ...rest] = detail.alerts
    const at = (s: number) => new Date(Date.parse(first.event_ts) + s * 1000).toISOString()
    const cancelled: ResidentAlert = {
      ...first,
      status: 'cancelled',
      acknowledged_at: at(60),
      ack_note: 'Synthetic note: calling the caregiver.',
      cancelled_at: at(120),
      cancelled_by_role: 'caregiver',
      cancelled_by: 'Test Caregiver A',
    }
    await renderLoaded('res-0003', NURSE_D, { ...detail, alerts: [cancelled, ...rest] })
    const row = historyRows()[0]
    expect(within(row).getByText('Cancelled')).toBeInTheDocument()
    expect(row).toHaveTextContent('Acknowledged by the care centre nurse,')
    expect(row).toHaveTextContent('Synthetic note: calling the caregiver.')
    expect(within(row).getByText(/^Cancelled by/)).toHaveTextContent(/^Cancelled by Test Caregiver A \(caregiver\), /)
    expect(row).not.toHaveTextContent(/Escalat|Sent to the care centre nurse/)
    expect(within(row).queryAllByRole('button')).toEqual([])
  })

  it('res-0014 shows the empty text', async () => {
    await renderLoaded('res-0014')
    expect(screen.getByRole('heading', { level: 2, name: 'Alert history (0)' })).toBeInTheDocument()
    expect(within(history()).getByText('No alerts for this resident in the last 30 days.')).toBeInTheDocument()
    expect(historyRows()).toEqual([])
  })

  it.each([
    ['doctor', TEST_USERS.doctor],
    ['nurse', TEST_USERS.nurse],
  ] as const)('does not repeat the resident line in rows (%s)', async (role, user) => {
    const detail = demoDetail('res-0003')
    await renderLoaded('res-0003', user, detail)
    historyRows().forEach((row) => {
      expect(row).not.toHaveTextContent(detail.resident.home_id)
      expect(row).not.toHaveTextContent('res-0003')
      // The nurse's action buttons name the resident in sr-only text; nothing else does.
      if (role === 'doctor') expect(row).not.toHaveTextContent('Test Resident C')
    })
  })

  it('the escalation countdown shows as m:ss on an open warning before its 5-minute escalation (res-0008)', async () => {
    await renderLoaded('res-0008', TEST_USERS.doctor)
    expect(within(historyRows()[0]).getByRole('timer')).toHaveTextContent(
      /^Escalates to the care centre nurse in [0-2]:[0-5]\d$/,
    )
  })

  it('an open critical alert shows "Sent to the care centre nurse at once"; closed rows show no escalation line', async () => {
    await renderLoaded('res-0003', TEST_USERS.doctor)
    const [open, ...closed] = historyRows()
    expect(open).toHaveTextContent('Sent to the care centre nurse at once')
    expect(open).not.toHaveTextContent('Escalated to the care centre nurse')
    expect(within(open).queryByRole('timer')).not.toBeInTheDocument()
    closed.forEach((row) => expect(row).not.toHaveTextContent(/Escalat|Sent to the care centre nurse/))
  })

  it('an open warning past its 5 minutes shows "Escalated to the care centre nurse" (res-0012)', async () => {
    const detail = demoDetail('res-0012')
    expect(detail.alerts[0]).toMatchObject({ alert_type: 'possible_fall', severity: 'warning', status: 'open' })
    await renderLoaded('res-0012', TEST_USERS.doctor, detail)
    const open = historyRows()[0]
    expect(open).toHaveTextContent('Escalated to the care centre nurse')
    expect(open).not.toHaveTextContent('at once')
    expect(within(open).queryByRole('timer')).not.toBeInTheDocument()
  })

  it('res-0007: the acknowledged hr_high alert was sent to the nurse at once, but shows no escalation line', async () => {
    const detail = demoDetail('res-0007')
    expect(detail.alerts[0]).toMatchObject({ alert_type: 'hr_high', status: 'acknowledged' })
    expect(detail.alerts[0].escalated_at).toBeDefined()
    await renderLoaded('res-0007', TEST_USERS.doctor, detail)
    expect(historyRows()[0]).toHaveTextContent('High heart rate')
    expect(historyRows()[0]).not.toHaveTextContent(/Escalat|Sent to the care centre nurse/)
  })

  it('announces an escalation that happens while the page is open', async () => {
    const detail = demoDetail('res-0008')
    const [first, ...rest] = detail.alerts
    const soon = { ...first, escalates_at: new Date(Date.now() + 1_200).toISOString() }
    await renderLoaded('res-0008', TEST_USERS.doctor, { ...detail, alerts: [soon, ...rest] })
    expect(announcer()).toBeEmptyDOMElement()
    await waitFor(() => expect(announcer()).toHaveTextContent('Escalated to the care centre nurse: Possible fall, Test Resident H.'))
    // The countdown ticks once a second, so it can trail the announcement slightly.
    await waitFor(() => expect(within(historyRows()[0]).getByText('Escalated to the care centre nurse')).toBeInTheDocument())
  })
})

// --- nurse alert actions -----------------------------------------------------

describe('nurse alert actions', () => {
  it('unresolved rows have Acknowledge/Resolve; resolved rows have none', async () => {
    await renderLoaded('res-0003')
    const rows = historyRows()
    expect(within(rows[0]).getByRole('button', { name: 'Acknowledge: Fall detected, Test Resident C' })).toBeInTheDocument()
    expect(within(rows[0]).getByRole('button', { name: 'Resolve: Fall detected, Test Resident C' })).toBeInTheDocument()
    rows.slice(1).forEach((row) => expect(within(row).queryAllByRole('button')).toEqual([]))
    expect(screen.getAllByRole('button', { name: /^(Acknowledge|Resolve)/ })).toHaveLength(2)
  })

  it('an acknowledged alert has Resolve only (res-0007)', async () => {
    await renderLoaded('res-0007')
    const first = historyRows()[0]
    expect(within(first).queryByRole('button', { name: /^Acknowledge/ })).not.toBeInTheDocument()
    expect(within(first).getByRole('button', { name: 'Resolve: High heart rate, Test Resident G' })).toBeInTheDocument()
  })

  it.each([
    ['nurse', NURSE_D],
    ['doctor', TEST_USERS.doctor],
  ] as const)(
    'a cancelled alert shows "Cancelled" and the cancelled line, with no buttons (%s)',
    async (_, user) => {
      const detail = demoDetail('res-0003')
      const [first, ...rest] = detail.alerts
      const cancelledAt = new Date(Date.parse(first.event_ts) + 30_000).toISOString()
      const cancelled: ResidentAlert = {
        ...first,
        status: 'cancelled',
        cancelled_at: cancelledAt,
        cancelled_by_role: 'resident',
      }
      await renderLoaded('res-0003', user, { ...detail, alerts: [cancelled, ...rest] })
      const row = historyRows()[0]
      expect(within(row).getByText('Cancelled')).toBeInTheDocument()
      expect(within(row).getByText('Cancelled').querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      const line = within(row).getByText(/^Cancelled by the resident/)
      expect(line).toHaveTextContent(/^Cancelled by the resident \("I'm OK"\), \d+ min ago/)
      expect(line.querySelector('time')).toHaveAttribute('datetime', cancelledAt)
      expect(row).not.toHaveTextContent(/Escalat/)
      expect(row).not.toHaveTextContent('Resolved by')
      expect(within(row).queryAllByRole('button')).toEqual([])
      expect(screen.queryAllByRole('button', { name: /^(Acknowledge|Resolve)/ })).toEqual([])
      // The page is still in the history count.
      expect(screen.getByRole('heading', { level: 2, name: `Alert history (${detail.alerts.length})` })).toBeInTheDocument()
    },
  )

  it.each([
    ['nurse', NURSE_D],
    ['doctor', TEST_USERS.doctor],
  ] as const)('res-0002: the demo cancelled history alert shows its line and no buttons (%s)', async (role, user) => {
    const detail = demoDetail('res-0002')
    const index = detail.alerts.findIndex((a) => a.status === 'cancelled')
    expect(index).toBeGreaterThanOrEqual(0)
    const cancelled = detail.alerts[index]
    expect(cancelled).toMatchObject({ alert_type: 'hr_high', cancelled_by_role: 'resident' })
    await renderLoaded('res-0002', user, detail)
    const row = historyRows()[index]
    expect(row).toHaveTextContent('High heart rate')
    expect(within(row).getByText('Cancelled')).toBeInTheDocument()
    const line = within(row).getByText(/^Cancelled by the resident/)
    // Raised 95 h ago (i = 1, k = 0) and cancelled 30 s later.
    expect(line).toHaveTextContent(/^Cancelled by the resident \("I'm OK"\), 3 days ago/)
    expect(line.querySelector('time')).toHaveAttribute('datetime', cancelled.cancelled_at)
    expect(row).not.toHaveTextContent('Acknowledged by')
    expect(row).not.toHaveTextContent('Resolved by')
    expect(within(row).queryAllByRole('button')).toEqual([])
    // Only open-list alerts get buttons, and only for the nurse.
    const openList = detail.alerts.filter((a) => a.status === 'open' || a.status === 'acknowledged')
    const resolveButtons = screen.queryAllByRole('button', { name: /^Resolve/ })
    expect(resolveButtons).toHaveLength(role === 'nurse' ? openList.length : 0)
  })

  it('res-0006: a history alert handled by a deleted account reads "Former staff member"', async () => {
    const detail = demoDetail('res-0006')
    const former = detail.alerts.filter((a) => a.resolved_by === 'Former staff member')
    expect(former).toHaveLength(1)
    await renderLoaded('res-0006', TEST_USERS.doctor, detail)
    const row = historyRows()[detail.alerts.indexOf(former[0])]
    expect(row).toHaveTextContent('Acknowledged by the care centre nurse,')
    expect(row).toHaveTextContent('Resolved by Former staff member,')
  })

  it('acknowledge, then resolve: by-lines, label, focus, and announcements', async () => {
    const { user } = await renderLoaded('res-0003', NURSE_D)
    const row = () => historyRows()[0]

    await user.click(screen.getByRole('button', { name: 'Acknowledge: Fall detected, Test Resident C' }))
    expect(within(row()).getByText('Acknowledged')).toBeInTheDocument()
    expect(row()).toHaveTextContent('Acknowledged by the care centre nurse, just now')
    expect(within(row()).queryByRole('button', { name: /^Acknowledge/ })).not.toBeInTheDocument()
    expect(announcer()).toHaveTextContent('Acknowledged: Fall detected, Test Resident C.')
    const resolve = screen.getByRole('button', { name: 'Resolve: Fall detected, Test Resident C' })
    await waitFor(() => expect(resolve).toHaveFocus())

    await user.click(resolve)
    expect(within(row()).getByText('Resolved')).toBeInTheDocument()
    expect(row()).toHaveTextContent('Resolved by Test Nurse D, just now')
    expect(row()).toHaveTextContent('Acknowledged by the care centre nurse, ')
    expect(within(row()).queryAllByRole('button')).toEqual([])
    expect(announcer()).toHaveTextContent('Resolved: Fall detected, Test Resident C.')
    const heading = screen.getByRole('heading', { level: 2, name: 'Alert history (4)' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(heading).toHaveAttribute('tabindex', '-1')
    // Still listed, not removed.
    expect(historyRows()).toHaveLength(4)
  })

  it('resolve straight from open moves focus to the Alert history heading', async () => {
    const { user } = await renderLoaded('res-0003', NURSE_D)
    await user.click(screen.getByRole('button', { name: 'Resolve: Fall detected, Test Resident C' }))
    expect(historyRows()[0]).toHaveTextContent('Resolved by Test Nurse D')
    expect(historyRows()[0]).not.toHaveTextContent('Acknowledged by')
    expect(announcer()).toHaveTextContent('Resolved: Fall detected, Test Resident C.')
    await waitFor(() => expect(screen.getByRole('heading', { level: 2, name: /^Alert history/ })).toHaveFocus())
  })

  it.each(['res-0003', 'res-0007', 'res-0008'])('the doctor sees no Acknowledge/Resolve buttons (%s)', async (id) => {
    await renderLoaded(id, TEST_USERS.doctor)
    expect(screen.queryAllByRole('button', { name: /Acknowledge|Resolve/ })).toEqual([])
  })
})

// --- thresholds (pattern 18) -------------------------------------------------

describe('thresholds (pattern 18)', () => {
  it('shows the default values read-only, with "Not changed since setup." (res-0003, no history)', async () => {
    await renderLoaded('res-0003')
    expect(limit('Heart rate')).toHaveTextContent('50 to 120 bpm')
    expect(limit('SpO2')).toHaveTextContent('At least 92%')
    expect(limit('Skin temperature')).toHaveTextContent('30.0 to 38.0 °C')
    expect(within(thresholds()).getByText('Not changed since setup.')).toBeInTheDocument()
    expect(thresholds()).not.toHaveTextContent('Last changed by')
    expect(screen.queryByRole('button', { name: 'Edit thresholds' })).not.toBeInTheDocument()
    expect(within(thresholds()).queryAllByRole('textbox')).toEqual([])
  })

  it('shows "Not changed since setup." when only one of updated_by/updated_at is present', async () => {
    const detail = demoDetail('res-0003')
    for (const partial of [{ updated_by: 'Test Doctor A' }, { updated_at: new Date().toISOString() }]) {
      const view = await renderLoaded('res-0003', TEST_USERS.nurse, {
        ...detail,
        thresholds: { ...detail.thresholds, ...partial },
      })
      expect(within(thresholds()).getByText('Not changed since setup.')).toBeInTheDocument()
      expect(thresholds()).not.toHaveTextContent('Last changed by')
      view.unmount()
    }
  })

  it.each(['res-0003', 'res-0006', 'res-0009', 'res-0012', 'res-0015', 'res-0018'])(
    '%s (no history) shows "Not changed since setup." and the defaults',
    async (id) => {
      await renderLoaded(id)
      expect(within(thresholds()).getByText('Not changed since setup.')).toBeInTheDocument()
      expect(limit('Heart rate')).toHaveTextContent('50 to 120 bpm')
      expect(limit('SpO2')).toHaveTextContent('At least 92%')
      expect(limit('Skin temperature')).toHaveTextContent('30.0 to 38.0 °C')
    },
  )

  it.each([
    ['res-0001', 'Test Doctor B'],
    ['res-0002', 'Test Doctor B'],
    ['res-0004', 'Test Doctor A'],
    ['res-0011', 'Test Doctor B'],
    ['res-0019', 'Test Doctor B'],
  ])('%s: "Last changed by %s", at the newest change history entry', async (id, name) => {
    const detail = demoDetail(id)
    await renderLoaded(id, TEST_USERS.nurse, detail)
    const byLine = within(thresholds()).getByText(/^Last changed by/)
    expect(byLine).toHaveTextContent(new RegExp(`^Last changed by ${name},`))
    expect(byLine.querySelector('time')).toHaveAttribute('datetime', detail.threshold_history[0].changed_at)
    expect(historyEntries()[0].querySelector('time')).toHaveAttribute(
      'datetime',
      detail.threshold_history[0].changed_at,
    )
  })

  it('res-0002 (Test Resident B) shows its own SpO2 limit "At least 88%", the rest default', async () => {
    await renderLoaded('res-0002')
    expect(limit('SpO2')).toHaveTextContent('At least 88%')
    expect(limit('Heart rate')).toHaveTextContent('50 to 120 bpm')
    expect(limit('Skin temperature')).toHaveTextContent('30.0 to 38.0 °C')
  })

  it.each(['res-0001', 'res-0011', 'res-0019'])('%s has the defaults (its history was set back)', async (id) => {
    await renderLoaded(id)
    expect(limit('Heart rate')).toHaveTextContent('50 to 120 bpm')
    expect(limit('SpO2')).toHaveTextContent('At least 92%')
    expect(limit('Skin temperature')).toHaveTextContent('30.0 to 38.0 °C')
  })

  it('the nurse sees "Only doctors can change thresholds." and no Edit button', async () => {
    await renderLoaded('res-0001')
    expect(within(thresholds()).getByText('Only doctors can change thresholds.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit thresholds' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('the doctor sees Edit thresholds and not the nurse note', async () => {
    await renderLoaded('res-0001', TEST_USERS.doctor)
    expect(within(thresholds()).getByRole('button', { name: 'Edit thresholds' })).toBeInTheDocument()
    expect(screen.queryByText('Only doctors can change thresholds.')).not.toBeInTheDocument()
  })

  it('Edit opens the form with the current values and focuses its first field', async () => {
    const { user } = await renderLoaded('res-0011', DOCTOR_C)
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    const first = screen.getByRole('textbox', { name: 'Heart rate, lower limit (bpm)' })
    await waitFor(() => expect(first).toHaveFocus())
    expect(first).toHaveValue('50')
    expect(screen.getByRole('textbox', { name: 'Heart rate, upper limit (bpm)' })).toHaveValue('120')
    expect(screen.getByRole('textbox', { name: 'SpO2, lower limit (%)' })).toHaveValue('92')
    expect(screen.getByRole('textbox', { name: 'Skin temperature, lower limit (°C)' })).toHaveValue('30.0')
    expect(screen.getByRole('textbox', { name: 'Skin temperature, upper limit (°C)' })).toHaveValue('38.0')
    expect(first).toHaveAccessibleDescription('Whole number from 20 to 250.')
    expect(screen.getByRole('textbox', { name: 'Skin temperature, lower limit (°C)' })).toHaveAccessibleDescription(
      'From 20 to 45, up to 1 decimal place.',
    )
    expect(screen.queryByRole('button', { name: 'Edit thresholds' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save thresholds' })).toHaveAttribute('type', 'submit')
  })

  it('Cancel returns focus to Edit without changing anything', async () => {
    const { user } = await renderLoaded('res-0001', DOCTOR_C)
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    const hrMax = screen.getByRole('textbox', { name: 'Heart rate, upper limit (bpm)' })
    await user.clear(hrMax)
    await user.type(hrMax, '140')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    const edit = screen.getByRole('button', { name: 'Edit thresholds' })
    await waitFor(() => expect(edit).toHaveFocus())
    expect(limit('Heart rate')).toHaveTextContent('50 to 120 bpm')
    expect(within(thresholds()).getByText(/^Last changed by Test Doctor B,/)).toBeInTheDocument()
    expect(historyEntries()).toHaveLength(2)
    expect(announcer()).not.toHaveTextContent('Thresholds saved.')

    await user.click(edit)
    expect(screen.getByRole('textbox', { name: 'Heart rate, upper limit (bpm)' })).toHaveValue('120')
  })

  it('Save with valid values updates the display, the by-line, announces, and returns focus', async () => {
    const { user } = await renderLoaded('res-0001', DOCTOR_C)
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    const set = async (name: string, value: string) => {
      const input = screen.getByRole('textbox', { name })
      await user.clear(input)
      await user.type(input, value)
    }
    await set('Heart rate, upper limit (bpm)', '115')
    await set('SpO2, lower limit (%)', '90')
    await set('Skin temperature, upper limit (°C)', '37.5')
    await user.click(screen.getByRole('button', { name: 'Save thresholds' }))

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(limit('Heart rate')).toHaveTextContent('50 to 115 bpm')
    expect(limit('SpO2')).toHaveTextContent('At least 90%')
    expect(limit('Skin temperature')).toHaveTextContent('30.0 to 37.5 °C')
    expect(within(thresholds()).getByText(/^Last changed by Test Doctor C,/)).toHaveTextContent('just now')
    expect(announcer()).toHaveTextContent('Thresholds saved.')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit thresholds' })).toHaveFocus())
  })

  it('a doctor saving on a resident with no history replaces "Not changed since setup." with the by-line', async () => {
    const { user } = await renderLoaded('res-0003', DOCTOR_C)
    expect(within(thresholds()).getByText('Not changed since setup.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    const spo2 = screen.getByRole('textbox', { name: 'SpO2, lower limit (%)' })
    await user.clear(spo2)
    await user.type(spo2, '90{Enter}')
    expect(limit('SpO2')).toHaveTextContent('At least 90%')
    expect(within(thresholds()).queryByText('Not changed since setup.')).not.toBeInTheDocument()
    const byLine = within(thresholds()).getByText(/^Last changed by Test Doctor C,/)
    expect(byLine).toHaveTextContent('just now')
    expect(byLine.querySelector('time')).not.toBeNull()
  })

  it('Enter in a field submits the form', async () => {
    const { user } = await renderLoaded('res-0001', DOCTOR_C)
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    const hrMin = screen.getByRole('textbox', { name: 'Heart rate, lower limit (bpm)' })
    await user.clear(hrMin)
    await user.type(hrMin, '45{Enter}')
    expect(limit('Heart rate')).toHaveTextContent('45 to 120 bpm')
  })

  it('invalid values show inline errors linked by aria-describedby and focus the first invalid field', async () => {
    const { user } = await renderLoaded('res-0001', DOCTOR_C)
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    const field = (name: string) => screen.getByRole('textbox', { name })
    await user.clear(field('Heart rate, lower limit (bpm)'))
    await user.clear(field('SpO2, lower limit (%)'))
    await user.type(field('SpO2, lower limit (%)'), '101')
    await user.clear(field('Skin temperature, upper limit (°C)'))
    await user.type(field('Skin temperature, upper limit (°C)'), '30.0')
    await user.click(screen.getByRole('button', { name: 'Save thresholds' }))

    const hrMin = field('Heart rate, lower limit (bpm)')
    await waitFor(() => expect(hrMin).toHaveFocus())
    expect(hrMin).toHaveAttribute('aria-invalid', 'true')
    expect(hrMin).toHaveAccessibleDescription('Whole number from 20 to 250. Enter a value.')
    expect(field('SpO2, lower limit (%)')).toHaveAccessibleDescription(
      'Whole number from 50 to 100. Enter a value from 50 to 100.',
    )
    expect(field('Skin temperature, upper limit (°C)')).toHaveAccessibleDescription(
      'From 20 to 45, up to 1 decimal place. Must be higher than the lower limit.',
    )
    expect(field('Heart rate, upper limit (bpm)')).not.toHaveAttribute('aria-invalid')
    // Nothing saved yet.
    expect(screen.queryByRole('button', { name: 'Edit thresholds' })).not.toBeInTheDocument()
    expect(announcer()).not.toHaveTextContent('Thresholds saved.')
  })

  it('focuses the first invalid field even when it is not the first field', async () => {
    const { user } = await renderLoaded('res-0001', DOCTOR_C)
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    const tempMin = screen.getByRole('textbox', { name: 'Skin temperature, lower limit (°C)' })
    await user.clear(tempMin)
    await user.type(tempMin, '31.25')
    await user.click(screen.getByRole('button', { name: 'Save thresholds' }))
    await waitFor(() => expect(tempMin).toHaveFocus())
    expect(tempMin).toHaveAccessibleDescription(expect.stringContaining('Enter a number with at most 1 decimal place.'))

    // Fixing it lets the save go through.
    await user.clear(tempMin)
    await user.type(tempMin, '31.5')
    await user.click(screen.getByRole('button', { name: 'Save thresholds' }))
    expect(limit('Skin temperature')).toHaveTextContent('31.5 to 38.0 °C')
  })
})

// --- threshold change history ----------------------------------------------

const entryLines = (entry: HTMLElement) =>
  Array.from(entry.querySelectorAll('ul > li')).map((li) => li.textContent)
const entryByLine = (entry: HTMLElement) => entry.querySelector('p') as HTMLElement

describe('threshold change history', () => {
  it('comes right after Vitals thresholds', async () => {
    await renderLoaded('res-0001')
    const h2s = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(h2s.slice(-2)).toEqual(['Vitals thresholds', 'Change history'])
    expect(
      within(changeHistory()).getByText('Earlier changes to the vitals thresholds, newest first.'),
    ).toBeInTheDocument()
  })

  it('res-0002: one entry, SpO2 lower limit from 92% to 88%, by Test Doctor B, 9 days ago', async () => {
    await renderLoaded('res-0002')
    const entries = historyEntries()
    expect(entries).toHaveLength(1)
    expect(entryByLine(entries[0])).toHaveTextContent(/^Changed by Test Doctor B, 9 days ago/)
    expect(entryLines(entries[0])).toEqual(['SpO2, lower limit: from 92% to 88%'])
    expect(limit('SpO2')).toHaveTextContent('At least 88%')
  })

  it('res-0019 (now generic): skin temperature lower limit 23 and 29 days ago, by Test Doctor B', async () => {
    await renderLoaded('res-0019')
    const entries = historyEntries()
    expect(entries).toHaveLength(2)
    expect(entryByLine(entries[0])).toHaveTextContent(/^Changed by Test Doctor B, 23 days ago/)
    expect(entryLines(entries[0])).toEqual(['Skin temperature, lower limit: from 29.5 °C to 30.0 °C'])
    expect(entryByLine(entries[1])).toHaveTextContent(/^Changed by Test Doctor B, 29 days ago/)
    expect(entryLines(entries[1])).toEqual(['Skin temperature, lower limit: from 30.0 °C to 29.5 °C'])
    expect(changeHistory()).not.toHaveTextContent('SpO2')
  })

  it('res-0001: skin temperature lower limit set back to 30.0 °C (5 days ago) after 29.5 °C (11 days ago)', async () => {
    await renderLoaded('res-0001')
    const entries = historyEntries()
    expect(entries).toHaveLength(2)
    expect(entryByLine(entries[0])).toHaveTextContent(/^Changed by Test Doctor B, 5 days ago/)
    expect(entryLines(entries[0])).toEqual(['Skin temperature, lower limit: from 29.5 °C to 30.0 °C'])
    expect(entryByLine(entries[1])).toHaveTextContent(/^Changed by Test Doctor B, 11 days ago/)
    expect(entryLines(entries[1])).toEqual(['Skin temperature, lower limit: from 30.0 °C to 29.5 °C'])
    expect(limit('Skin temperature')).toHaveTextContent('30.0 to 38.0 °C')
  })

  it('res-0005: two entries newest first in an ordered list, one line per changed value', async () => {
    const detail = demoDetail('res-0005')
    await renderLoaded('res-0005', TEST_USERS.nurse, detail)
    const entries = historyEntries()
    expect(entries).toHaveLength(2)
    entries.forEach((e) => expect(e.tagName).toBe('LI'))
    expect(entryByLine(entries[0])).toHaveTextContent(/^Changed by Test Doctor B, 8 days ago/)
    expect(entryLines(entries[0])).toEqual([
      'Heart rate, lower limit: from 45 bpm to 50 bpm',
      'Heart rate, upper limit: from 125 bpm to 120 bpm',
    ])
    expect(entryByLine(entries[1])).toHaveTextContent(/^Changed by Test Doctor B, 11 days ago/)
    expect(entryLines(entries[1])).toEqual([
      'Heart rate, lower limit: from 50 bpm to 45 bpm',
      'Heart rate, upper limit: from 120 bpm to 125 bpm',
    ])
    const times = entries.map((e) => e.querySelector('time')?.getAttribute('datetime'))
    expect(times).toEqual(detail.threshold_history.map((h) => h.changed_at))
    expect(limit('Heart rate')).toHaveTextContent('50 to 120 bpm')
  })

  it('sorts entries newest first even if the data is not', async () => {
    const detail = demoDetail('res-0005')
    const reversed = [...detail.threshold_history].reverse()
    await renderLoaded('res-0005', TEST_USERS.nurse, { ...detail, threshold_history: reversed })
    const times = historyEntries().map((e) => e.querySelector('time')?.getAttribute('datetime') as string)
    expect(times).toEqual(detail.threshold_history.map((h) => h.changed_at))
    expect([...times].sort().reverse()).toEqual(times)
  })

  it.each(['res-0003', 'res-0006', 'res-0009', 'res-0012', 'res-0015', 'res-0018'])(
    '%s shows the empty state',
    async (id) => {
      await renderLoaded(id)
      expect(
        within(changeHistory()).getByText('No changes to these thresholds have been recorded.'),
      ).toBeInTheDocument()
      expect(changeHistory().querySelector('ol')).toBeNull()
    },
  )

  it.each([
    ['nurse', TEST_USERS.nurse],
    ['doctor', TEST_USERS.doctor],
  ] as const)('is read-only for the %s: no buttons or inputs', async (_, user) => {
    await renderLoaded('res-0002', user)
    expect(within(changeHistory()).queryAllByRole('button')).toEqual([])
    expect(within(changeHistory()).queryAllByRole('textbox')).toEqual([])
    expect(changeHistory().querySelectorAll('input, select, textarea, button')).toHaveLength(0)
  })

  it('stays read-only while the doctor has the thresholds form open', async () => {
    const { user } = await renderLoaded('res-0002', DOCTOR_C)
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    expect(changeHistory().querySelectorAll('input, select, textarea, button')).toHaveLength(0)
  })

  it('a doctor saving thresholds does not add a history entry; values and "Last changed by" update', async () => {
    const { user } = await renderLoaded('res-0002', DOCTOR_C)
    const before = historyEntries().map((e) => e.textContent)
    expect(before).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    const hrMax = screen.getByRole('textbox', { name: 'Heart rate, upper limit (bpm)' })
    await user.clear(hrMax)
    await user.type(hrMax, '125{Enter}')

    expect(limit('Heart rate')).toHaveTextContent('50 to 125 bpm')
    expect(within(thresholds()).getByText(/^Last changed by Test Doctor C,/)).toHaveTextContent('just now')
    expect(historyEntries().map((e) => e.textContent)).toEqual(before)
    expect(changeHistory()).not.toHaveTextContent('Test Doctor C')
  })

  it('has no axe violations with entries and when empty', async () => {
    const first = await renderLoaded('res-0002', TEST_USERS.doctor)
    expect(await axeViolations(first.container)).toEqual([])
    first.unmount()
    const empty = await renderLoaded('res-0003', TEST_USERS.nurse)
    expect(await axeViolations(empty.container)).toEqual([])
  })
})

// --- privacy -----------------------------------------------------------------

describe('privacy', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('document.title is "SilverCare - Resident details" and never has the resident name', async () => {
    await renderLoaded('res-0003')
    await waitFor(() => expect(document.title).toBe('SilverCare - Resident details'))
    expect(document.title).not.toContain('Test Resident')
  })

  it('keeps the title while loading and on not found', async () => {
    renderPage('res-9999')
    await waitFor(() => expect(document.title).toBe('SilverCare - Resident details'))
    await screen.findByText('Resident not found')
    expect(document.title).toBe('SilverCare - Resident details')
  })

  it('writes nothing to storage and never calls console.log, through nurse and doctor actions', async () => {
    localStorage.clear()
    sessionStorage.clear()
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const log = vi.spyOn(console, 'log')

    const nurse = await renderLoaded('res-0003', NURSE_D)
    await nurse.user.click(screen.getByRole('button', { name: 'Acknowledge: Fall detected, Test Resident C' }))
    await nurse.user.click(screen.getByRole('button', { name: 'Resolve: Fall detected, Test Resident C' }))
    nurse.unmount()

    const doctor = await renderLoaded('res-0003', DOCTOR_C)
    await doctor.user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    await doctor.user.type(screen.getByRole('textbox', { name: 'SpO2, lower limit (%)' }), '{Backspace}0{Enter}')
    expect(limit('SpO2')).toHaveTextContent('At least 90%')

    expect(setItem).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(log).not.toHaveBeenCalled()
    expect(location()).toBe('/residents/res-0003')
  })
})

// --- accessibility -----------------------------------------------------------

describe('accessibility', () => {
  it.each([
    ['nurse', TEST_USERS.nurse],
    ['doctor', TEST_USERS.doctor],
  ] as const)('has no axe violations for the %s', async (_, user) => {
    const { container } = await renderLoaded('res-0003', user)
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations for the nurse with a cancelled alert in the history (res-0002)', async () => {
    const { container } = await renderLoaded('res-0002', TEST_USERS.nurse)
    expect(within(history()).getByText(/^Cancelled by the resident/)).toBeInTheDocument()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations with the thresholds form open, and with errors', async () => {
    const { container, user } = await renderLoaded('res-0001', TEST_USERS.doctor)
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    expect(await axeViolations(container)).toEqual([])
    await user.clear(screen.getByRole('textbox', { name: 'Heart rate, lower limit (bpm)' }))
    await user.click(screen.getByRole('button', { name: 'Save thresholds' }))
    await screen.findByText('Enter a value.')
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations while loading, on error, and on not found', async () => {
    const pending = deferred<ResidentDetail | null>()
    residentDetailLoader.mockReturnValueOnce(pending.promise)
    const loading = renderPage('res-0001')
    expect(await axeViolations(loading.container)).toEqual([])
    pending.resolve(null)
    await screen.findByText('Resident not found')
    expect(await axeViolations(loading.container)).toEqual([])
    loading.unmount()

    residentDetailLoader.mockRejectedValueOnce(new Error('boom'))
    const error = renderPage('res-0001')
    await screen.findByRole('alert')
    expect(await axeViolations(error.container)).toEqual([])
  })

  it('has no axe violations with no devices and no alerts', async () => {
    const detail = demoDetail('res-0014')
    const { container } = await renderLoaded('res-0014', TEST_USERS.nurse, { ...detail, wearable: null, bedroom: null })
    expect(await axeViolations(container)).toEqual([])
  })

  it('headings are in order: h1 page, h2 resident and sections, h3 device subsections', async () => {
    const detail = demoDetail('res-0001')
    await renderLoaded('res-0001', TEST_USERS.nurse, detail)
    const headings = screen.getAllByRole('heading').map((h) => [Number(h.tagName.slice(1)), h.textContent])
    const w = detail.wearable!.device_id
    const b = detail.bedroom!.device_id
    expect(headings).toEqual([
      [1, 'Resident details'],
      [2, 'Test Resident A'],
      [2, 'Devices'],
      [3, `Wearable · ${w}`],
      [3, `Bedroom sensor · ${b}`],
      [2, 'Latest readings'],
      [3, `Wearable · ${w}`],
      [3, `Bedroom sensor · ${b}`],
      [2, 'Alert history (1)'],
      [2, 'Vitals thresholds'],
      [2, 'Change history'],
    ])
    const levels = headings.map(([level]) => level as number)
    levels.slice(1).forEach((level, i) => expect(level - levels[i]).toBeLessThanOrEqual(1))
  })

  it('every status and severity badge, lifecycle label and connection state has text', async () => {
    const { container } = await renderLoaded('res-0003', TEST_USERS.doctor)
    const labels: Record<string, string> = {
      ok: 'OK',
      attention: 'Attention',
      urgent: 'Urgent',
      warning: 'Warning',
      critical: 'Critical',
    }
    const badges = container.querySelectorAll('[data-tone]')
    expect(badges.length).toBe(1 + historyRows().length)
    badges.forEach((badge) => {
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(badge).toHaveTextContent(labels[badge.getAttribute('data-tone') as string])
    })
    historyRows().forEach((row) => expect(row).toHaveTextContent(/Open|Acknowledged|Cancelled|Resolved/))
    for (const name of [/^Wearable/, /^Bedroom sensor/]) {
      expect(fact(deviceBox(name), 'Connection')).toHaveTextContent(/^(Online|Offline)$/)
    }
  })

  it('there is exactly one polite live region and the alert list is not inside it', async () => {
    const { container } = await renderLoaded('res-0003')
    const live = container.querySelectorAll('[aria-live]')
    expect(live).toHaveLength(1)
    expect(live[0]).toHaveAttribute('aria-live', 'polite')
    expect(live[0]).toBeEmptyDOMElement()
    expect(history().closest('[aria-live]')).toBeNull()
  })
})
