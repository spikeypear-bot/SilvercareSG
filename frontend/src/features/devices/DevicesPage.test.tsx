import { render, screen, waitFor, within } from '@testing-library/react'
import { BatteryCharging } from 'lucide-react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { TEST_USERS, deferred } from '../../test/fixtures'
import { deviceFleetLoader, fleetDevice, resetDeviceFleetLoader, spareDevice } from '../../test/fleetMock'
import { renderWithAuth } from '../../test/render'
import type { AuthUser } from '../../types/auth'
import type { FleetDevice } from '../../types/devices'
import { createDemoData, createDeviceFleet } from '../dashboard/demoData'
import { DevicesPage } from './DevicesPage'

vi.mock('../dashboard/useDashboardData', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../dashboard/useDashboardData')>()
  const { deviceFleetLoader } = await import('../../test/fleetMock')
  return { ...actual, useDeviceFleet: () => actual.useDeviceFleet(deviceFleetLoader) }
})


beforeEach(() => {
  resetDeviceFleetLoader()
})

function renderPage(user: AuthUser = TEST_USERS.nurse) {
  const ue = userEvent.setup()
  const view = renderWithAuth(<DevicesPage />, { user, path: '/devices', route: '/devices' })
  return { user: ue, ...view }
}

async function renderLoaded(devices?: FleetDevice[], user?: AuthUser) {
  if (devices) deviceFleetLoader.mockResolvedValue(devices)
  const ctx = renderPage(user)
  await screen.findByRole('group', { name: 'Choose which devices the list shows' })
  return ctx
}

// --- queries ---------------------------------------------------------------

const tileGroup = () => screen.getByRole('group', { name: 'Choose which devices the list shows' })
const tile = (label: string) => within(tileGroup()).getByRole('button', { name: new RegExp(`^${label}`) })
const tileValue = (label: string) =>
  within(tile(label)).getByText(label, { selector: 'span' }).nextElementSibling?.textContent
const announcer = () => screen.getByRole('status')
const table = () => screen.getByRole('table')
const bodyRows = () => within(table()).getAllByRole('row').slice(1)
const rowIds = () => bodyRows().map((row) => within(row).getByRole('rowheader').textContent)
const rowFor = (deviceId: string) =>
  within(table()).getByRole('rowheader', { name: deviceId }).closest('tr') as HTMLElement
/** Cells after the row header: Type, Assigned to, Status, Battery, Last seen, Firmware, Alert limits. */
const cells = (deviceId: string) => {
  const [type, assigned, status, battery, lastSeen, firmware, limits] = within(rowFor(deviceId)).getAllByRole('cell')
  return { type, assigned, status, battery, lastSeen, firmware, limits }
}
const listHeading = () => screen.getAllByRole('heading', { level: 2 }).find((h) => h.id !== 'device-stats-heading')
const location = () => screen.getByTestId('location').textContent

// Demo resident lookups, by letter (Test Resident A = res-0001).
const demo = createDemoData(new Date())
const resident = (letter: string) => {
  const r = demo.residents.find((x) => x.name === `Test Resident ${letter}`)
  if (!r) throw new Error(`No demo resident ${letter}`)
  return r
}
const wearableOf = (letter: string) => resident(letter).wearable.device_id
const bedroomOf = (letter: string) => resident(letter).bedroom.device_id

const pad = (n: number) => String(n).padStart(4, '0')

/** Exact triage order of the demo fleet. */
const DEMO_ORDER: string[] = [
  // Offline, longest silent first.
  bedroomOf('Q'), // 2 days
  bedroomOf('F'), // 26 h
  bedroomOf('O'), // 185 min
  bedroomOf('I'), // 70 min
  wearableOf('O'), // 52 min
  wearableOf('L'), // 18 min
  // Low battery, lowest first.
  bedroomOf('D'), // 9 %
  wearableOf('M'), // 12 %
  wearableOf('F'), // 15 %
  // The rest by device_id.
  ...[1, 2, 3, 4, 6, 7, 9, 12, 13, 15, 16, 17, 18, 19, 20].map((n) => `bedroom-${pad(n)}`),
  ...[1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 20].map((n) => `wearable-${pad(n)}`),
]

// ---------------------------------------------------------------------------

describe('roles', () => {
  it.each<[string, AuthUser]>([
    ['nurse', TEST_USERS.nurse],
    ['doctor', TEST_USERS.doctor],
    ['nurse + doctor', { ...TEST_USERS.doctor, groups: ['doctor', 'nurse'] }],
  ])('a %s user sees the Devices page and the fleet is loaded', async (_, user) => {
    await renderLoaded(undefined, user)
    expect(screen.getByRole('heading', { level: 1, name: 'Devices' })).toBeInTheDocument()
    expect(table()).toBeInTheDocument()
    expect(deviceFleetLoader).toHaveBeenCalledTimes(1)
  })

  it.each([['caregiver', TEST_USERS.caregiver]] as const)('a %s user gets the staff-only notice and nothing is loaded', async (_, user) => {
    const { user: ue } = renderPage(user)
    expect(screen.getByRole('heading', { level: 1, name: 'Staff only' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'This page is for care staff.' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: "Your account isn't set up yet" })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Devices' })).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Staff area' })).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText(/Test Resident/)).not.toBeInTheDocument()
    expect(screen.queryByText(/wearable-|bedroom-/)).not.toBeInTheDocument()
    expect(deviceFleetLoader).not.toHaveBeenCalled()
    await waitFor(() => expect(document.title).toBe('SilverCare - Staff only'))

    await ue.click(screen.getByRole('link', { name: 'Back to my dashboard' }))
    expect(await screen.findByText('Dashboard screen')).toBeInTheDocument()
    expect(location()).toBe('/')
    expect(deviceFleetLoader).not.toHaveBeenCalled()
  })

  it.each([
    ['no group', TEST_USERS.noGroup],
    ['only the removed "family" group', TEST_USERS.family],
  ] as const)('a user with %s gets the no-access page and nothing is loaded', async (_, user) => {
    renderPage(user)
    expect(screen.getByRole('heading', { level: 1, name: "Your account isn't set up yet" })).toBeInTheDocument()
    expect(screen.queryByText('This page is for care staff.')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(deviceFleetLoader).not.toHaveBeenCalled()
    await waitFor(() => expect(document.title).toBe('SilverCare - No access'))
  })
})

describe('layout', () => {
  it('has the "Devices" header, the Staff area tabs above the banner, and no greeting', async () => {
    await renderLoaded()
    expect(screen.getByRole('heading', { level: 1, name: 'Devices' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Staff area' })
    expect(within(nav).getByRole('link', { name: 'Devices' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).not.toHaveAttribute('aria-current')
    expect(screen.queryByRole('note', { name: 'Demo data' })).not.toBeInTheDocument()
    expect(screen.queryByText(/^Good (morning|afternoon|evening)/)).not.toBeInTheDocument()
  })

  it('the Dashboard tab goes back to /', async () => {
    const { user } = await renderLoaded()
    await user.click(screen.getByRole('link', { name: 'Dashboard' }))
    expect(await screen.findByText('Dashboard screen')).toBeInTheDocument()
    expect(location()).toBe('/')
  })
})

describe('page states', () => {
  it('shows the loading skeleton with a "Loading devices" status, header, and nav (no demo banner)', () => {
    deviceFleetLoader.mockReturnValue(deferred<FleetDevice[]>().promise)
    const { container } = renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('Loading devices')
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
    expect(screen.getByRole('heading', { level: 1, name: 'Devices' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Staff area' })).toBeInTheDocument()
    expect(screen.queryByRole('note', { name: 'Demo data' })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Choose which devices the list shows' })).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('then shows the tiles and table, and the loading status is gone', async () => {
    const pending = deferred<FleetDevice[]>()
    deviceFleetLoader.mockReturnValue(pending.promise)
    renderPage()
    pending.resolve(createDeviceFleet(new Date()))
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.queryByText('Loading devices')).not.toBeInTheDocument()
  })

  it('shows "We couldn\'t load the devices." with Try again, which reloads', async () => {
    deviceFleetLoader.mockRejectedValueOnce(new Error('boom'))
    const { user } = renderPage()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("We couldn't load the devices.")
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Choose which devices the list shows' })).not.toBeInTheDocument()
    expect(screen.queryByRole('note', { name: 'Demo data' })).not.toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Staff area' })).toBeInTheDocument()

    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(deviceFleetLoader).toHaveBeenCalledTimes(2)
  })

  it('shows the loading state again while retrying', async () => {
    deviceFleetLoader.mockRejectedValueOnce(new Error('boom'))
    deviceFleetLoader.mockReturnValueOnce(deferred<FleetDevice[]>().promise)
    const { user } = renderPage()
    await user.click(within(await screen.findByRole('alert')).getByRole('button', { name: 'Try again' }))
    expect(screen.getByRole('status')).toHaveTextContent('Loading devices')
  })

  it('an empty fleet shows "No devices yet", tiles at 0, and no table', async () => {
    await renderLoaded([])
    expect(screen.getByText('No devices yet')).toBeInTheDocument()
    expect(screen.getByText('Devices appear here once they are registered.')).toBeInTheDocument()
    for (const label of ['All devices', 'Offline', 'Low battery', 'Reporting normally']) {
      expect(tileValue(label)).toBe('0')
    }
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show all devices' })).not.toBeInTheDocument()
  })

  it.each([
    ['Offline', 'All devices are online.', [fleetDevice({ device_id: 'wearable-test-001' })]],
    ['Low battery', 'No devices are below 20%.', [fleetDevice({ device_id: 'wearable-test-001' })]],
    ['Reporting normally', 'No devices are reporting normally.', [spareDevice()]],
  ] as const)('the %s filter with no matches says "%s" and offers Show all devices', async (label, text, devices) => {
    const { user } = await renderLoaded([...devices])
    expect(tileValue(label)).toBe('0')
    await user.click(tile(label))
    expect(screen.getByRole('heading', { level: 2, name: `${label} (0)` })).toBeInTheDocument()
    expect(screen.getByText(text)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show all devices' }))
    expect(tile('All devices')).toHaveAttribute('aria-pressed', 'true')
    expect(tile(label)).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('heading', { level: 2, name: `All devices (${devices.length})` })).toBeInTheDocument()
    expect(bodyRows()).toHaveLength(devices.length)
    expect(announcer()).toHaveTextContent(`Showing ${devices.length} of ${devices.length} devices: all.`)
    expect(screen.queryByRole('button', { name: 'Show all devices' })).not.toBeInTheDocument()
  })

  it('the All devices view never offers Show all devices', async () => {
    await renderLoaded()
    expect(screen.queryByRole('button', { name: 'Show all devices' })).not.toBeInTheDocument()
  })
})

describe('tiles', () => {
  it('show the demo counts: 40 total, 6 offline, 3 low battery, 31 reporting normally', async () => {
    await renderLoaded()
    expect(within(tileGroup()).getAllByRole('button')).toHaveLength(4)
    expect(tileValue('All devices')).toBe('40')
    expect(tileValue('Offline')).toBe('6')
    expect(tileValue('Low battery')).toBe('3')
    expect(tileValue('Reporting normally')).toBe('31')
    expect(within(tile('Offline')).getByText('No status for 15 min')).toBeInTheDocument()
    expect(within(tile('Low battery')).getByText('Below 20%')).toBeInTheDocument()
    expect(within(tile('Reporting normally')).getByText('Online, battery OK')).toBeInTheDocument()
  })

  it('All devices is pressed by default and the table lists all 40', async () => {
    await renderLoaded()
    expect(tile('All devices')).toHaveAttribute('aria-pressed', 'true')
    for (const label of ['Offline', 'Low battery', 'Reporting normally']) {
      expect(tile(label)).toHaveAttribute('aria-pressed', 'false')
    }
    expect(listHeading()).toHaveTextContent('All devices (40)')
    expect(bodyRows()).toHaveLength(40)
    expect(announcer()).toBeEmptyDOMElement()
  })

  it.each([
    ['Offline', 6, 'offline', DEMO_ORDER.slice(0, 6)],
    ['Low battery', 3, 'low battery', DEMO_ORDER.slice(6, 9)],
    ['Reporting normally', 31, 'reporting normally', DEMO_ORDER.slice(9)],
  ] as const)('clicking "%s" filters to %i devices, presses the tile, and announces it', async (label, n, spoken, ids) => {
    const { user } = await renderLoaded()
    await user.click(tile(label))
    expect(tile(label)).toHaveAttribute('aria-pressed', 'true')
    expect(tile('All devices')).toHaveAttribute('aria-pressed', 'false')
    expect(listHeading()).toHaveTextContent(`${label} (${n})`)
    expect(table()).toHaveAccessibleName(`${label}, ${n} devices`)
    expect(rowIds()).toEqual(ids)
    expect(announcer()).toHaveTextContent(`Showing ${n} of 40 devices: ${spoken}.`)
    expect(screen.getAllByRole('status')).toHaveLength(1)

    await user.click(tile('All devices'))
    expect(tile('All devices')).toHaveAttribute('aria-pressed', 'true')
    expect(bodyRows()).toHaveLength(40)
    expect(announcer()).toHaveTextContent('Showing 40 of 40 devices: all.')
  })

  it('exactly one tile is pressed at any time', async () => {
    const { user } = await renderLoaded()
    for (const label of ['Offline', 'Low battery', 'Reporting normally', 'All devices', 'Offline']) {
      await user.click(tile(label))
      const pressed = within(tileGroup())
        .getAllByRole('button')
        .filter((b) => b.getAttribute('aria-pressed') === 'true')
      expect(pressed).toEqual([tile(label)])
    }
  })

  it.each(['{Enter}', ' '])('works with the keyboard (%j)', async (key) => {
    const { user } = await renderLoaded()
    tile('Low battery').focus()
    await user.keyboard(key)
    expect(tile('Low battery')).toHaveAttribute('aria-pressed', 'true')
    expect(tile('Low battery')).toHaveFocus()
    expect(bodyRows()).toHaveLength(3)
    expect(announcer()).toHaveTextContent('Showing 3 of 40 devices: low battery.')
  })

  it('can be reached with Tab from the Staff area tabs', async () => {
    const { user } = await renderLoaded()
    screen.getByRole('link', { name: 'Devices' }).focus()
    await user.tab()
    expect(tile('All devices')).toHaveFocus()
    await user.tab()
    expect(tile('Offline')).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(bodyRows()).toHaveLength(6)
  })

  it('choosing the same tile again re-announces (the live region text changes)', async () => {
    const { user } = await renderLoaded()
    await user.click(tile('Offline'))
    const first = announcer().textContent
    await user.click(tile('Offline'))
    const second = announcer().textContent
    expect(second).not.toBe(first)
    expect(announcer()).toHaveTextContent('Showing 6 of 40 devices: offline.')
  })
})

describe('table', () => {
  it('has a caption, a heading with the count, and the 8 columns with the device as row header', async () => {
    await renderLoaded()
    expect(table()).toHaveAccessibleName('All devices, 40 devices')
    expect(table().querySelector('caption')).toHaveClass('sr-only')
    expect(within(table()).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Device',
      'Type',
      'Assigned to',
      'Status',
      'Battery',
      'Last seen',
      'Firmware',
      'Alert limits',
    ])
    within(table())
      .getAllByRole('columnheader')
      .forEach((h) => expect(h).toHaveAttribute('scope', 'col'))
    bodyRows().forEach((row) => {
      expect(within(row).getByRole('rowheader')).toHaveAttribute('scope', 'row')
      expect(within(row).getAllByRole('cell')).toHaveLength(7)
    })
  })

  it('sorts the demo fleet for triage, in this exact order', async () => {
    await renderLoaded()
    expect(DEMO_ORDER).toHaveLength(40)
    expect(new Set(DEMO_ORDER).size).toBe(40)
    expect(rowIds()).toEqual(DEMO_ORDER)
  })

  it('sorts the same way whatever order the data arrives in', async () => {
    await renderLoaded([...createDeviceFleet(new Date())].reverse())
    expect(rowIds()).toEqual(DEMO_ORDER)
  })

  it('offline rows show "Offline" and how long ago they were last seen', async () => {
    await renderLoaded()
    const expected: Array<[string, string]> = [
      [bedroomOf('Q'), '2 days ago'],
      [bedroomOf('F'), '1 day ago'],
      [bedroomOf('O'), '3 h ago'],
      [bedroomOf('I'), '1 h ago'],
      [wearableOf('O'), '52 min ago'],
      [wearableOf('L'), '18 min ago'],
    ]
    for (const [id, ago] of expected) {
      const c = cells(id)
      expect(c.status).toHaveTextContent(/^Offline$/)
      expect(c.status.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(c.lastSeen).toHaveTextContent(ago)
    }
  })

  it('a row shows type, linked resident with home ID, status, battery, last seen, and firmware', async () => {
    const fleet = createDeviceFleet(new Date())
    await renderLoaded(fleet)
    const m = resident('M')
    const device = fleet.find((d) => d.device_id === m.wearable.device_id) as FleetDevice
    const c = cells(m.wearable.device_id)

    expect(c.type).toHaveTextContent(/^Wearable$/)
    expect(c.type.querySelector('svg[aria-hidden="true"]')).not.toBeNull()

    const link = within(c.assigned).getByRole('link')
    expect(link).toHaveAccessibleName('Test Resident M')
    expect(link).toHaveAttribute('href', '/residents/res-0013')
    expect(c.assigned).toHaveTextContent(m.home_id)
    expect(within(c.assigned).getByText('Home')).toHaveClass('sr-only')

    expect(c.status).toHaveTextContent(/^Online$/)
    expect(c.battery).toHaveTextContent(/^12%\s?· Low$/)
    expect(c.lastSeen.querySelector('time')).toHaveAttribute('datetime', device.last_seen)
    expect(c.lastSeen).toHaveTextContent(/^2 min ago/)
    expect(c.firmware).toHaveTextContent(/^1\.0\.0$/)
  })

  it('bedroom sensors read "Bedroom sensor"; mains-powered ones read "Mains powered"', async () => {
    await renderLoaded()
    const c = cells(bedroomOf('A'))
    expect(c.type).toHaveTextContent(/^Bedroom sensor$/)
    expect(c.battery).toHaveTextContent(/^Mains powered$/)
    expect(c.battery.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    expect(c.battery).not.toHaveTextContent('Low')
  })

  it('low batteries read "· Low" as text; others show only the percentage', async () => {
    await renderLoaded()
    expect(cells(bedroomOf('D')).battery).toHaveTextContent(/^9%\s?· Low$/)
    expect(cells(wearableOf('M')).battery).toHaveTextContent(/^12%\s?· Low$/)
    expect(cells(wearableOf('F')).battery).toHaveTextContent(/^15%\s?· Low$/)
    expect(cells(wearableOf('T')).battery).toHaveTextContent(/^27%$/)
    expect(cells(wearableOf('D')).battery).toHaveTextContent(/^45%$/)
  })

  it('the low-battery text has a space before the dot ("12% · Low"), as the spec says', async () => {
    // The percentage and "· Low" are separate flex items; without a space in the text,
    // copied text and some screen readers get "12%· Low".
    await renderLoaded()
    expect(cells(wearableOf('M')).battery.textContent).toBe('12% · Low')
  })

  it('a charging wearable shows the charging battery icon', async () => {
    await renderLoaded()
    const reference = render(<BatteryCharging />)
    const chargingShape = reference.container.querySelector('svg')?.innerHTML
    reference.unmount()
    const iconShape = (cell: HTMLElement) => cell.querySelector('svg')?.innerHTML

    const battery = cells(wearableOf('J')).battery
    expect(battery).toHaveTextContent(/^58%/)
    expect(chargingShape).toBeTruthy()
    expect(iconShape(battery)).toBe(chargingShape)
    expect(iconShape(cells(wearableOf('A')).battery)).not.toBe(chargingShape)
  })

  it('a charging wearable says "Charging" in text, not only with a hidden icon', async () => {
    await renderLoaded()
    // The charging icon is aria-hidden, so without text screen-reader users can't tell.
    expect(cells(wearableOf('J')).battery).toHaveTextContent(/charging/i)
  })

  it('every resident link uses the opaque resident ID only, never the name', async () => {
    await renderLoaded()
    const links = within(table()).getAllByRole('link')
    expect(links).toHaveLength(40)
    links.forEach((link) => {
      expect(link.getAttribute('href')).toMatch(/^\/residents\/res-\d{4}$/)
      expect(link).toHaveAccessibleName(/^Test Resident [A-T]$/)
    })
  })

  it('clicking a resident link opens their resident page', async () => {
    const { user } = await renderLoaded()
    await user.click(within(cells(wearableOf('M')).assigned).getByRole('link'))
    expect(location()).toBe('/residents/res-0013')
  })

  it('an unassigned spare that never reported reads "Not assigned", "Never", and "Unknown", and sorts first', async () => {
    const spare = spareDevice({ device_id: 'bedroom-test-spare-001' })
    const ok = fleetDevice({ device_id: 'wearable-test-001' })
    const offline = fleetDevice({
      device_id: 'wearable-test-002',
      online: false,
      last_seen: new Date(Date.now() - 3 * 24 * 60 * 60_000).toISOString(),
    })
    await renderLoaded([ok, offline, spare])
    expect(rowIds()).toEqual(['bedroom-test-spare-001', 'wearable-test-002', 'wearable-test-001'])

    const c = cells('bedroom-test-spare-001')
    expect(c.assigned).toHaveTextContent(/^Not assigned$/)
    expect(within(c.assigned).queryByRole('link')).not.toBeInTheDocument()
    expect(c.status).toHaveTextContent(/^Offline$/)
    expect(c.lastSeen).toHaveTextContent(/^Never$/)
    expect(c.lastSeen.querySelector('time')).toBeNull()
    expect(c.firmware).toHaveTextContent(/^Unknown$/)
    expect(tileValue('Offline')).toBe('2')
    expect(tileValue('Reporting normally')).toBe('1')
  })

  it('a never-reported spare wearable does not claim to be mains powered', async () => {
    // battery_pct is null both for mains power and for "never reported" (types/devices.ts);
    // a wearable is never mains powered.
    await renderLoaded([spareDevice({ device_id: 'wearable-test-spare-001', device_type: 'wearable' })])
    expect(cells('wearable-test-spare-001').battery).not.toHaveTextContent('Mains powered')
  })

  it('is read-only: no buttons or form controls in the list', async () => {
    await renderLoaded()
    const section = table().closest('section') as HTMLElement
    expect(within(section).queryAllByRole('button')).toEqual([])
    expect(within(section).queryAllByRole('checkbox')).toEqual([])
    expect(within(section).queryAllByRole('textbox')).toEqual([])
    expect(screen.queryByRole('button', { name: /restart|reboot|unassign|assign|delete|remove/i })).toBeNull()
  })

  it('the table is not inside a live region', async () => {
    await renderLoaded()
    expect(table().closest('[aria-live]')).toBeNull()
  })
})

describe('alert limits (criteria 16-17)', () => {
  const IN_USE = { hr_bpm: { min: 45, max: 130 }, spo2_pct: { min: 88 } }
  const NEW = { hr_bpm: { min: 45, max: 110 }, spo2_pct: { min: 88 } }
  /** The Alert limits cell (the row's last cell). */
  const limitsCell = (deviceId: string) => within(rowFor(deviceId)).getAllByRole('cell').at(-1) as HTMLElement
  const NUMBERS = /HR|bpm|SpO|\d+ %/

  const fleet = () => [
    fleetDevice({ device_id: 'wearable-test-001', alert_limits: { status: 'in_sync', desired: IN_USE, reported: IN_USE } }),
    fleetDevice({ device_id: 'wearable-test-002', alert_limits: { status: 'pending', desired: NEW, reported: IN_USE } }),
    fleetDevice({ device_id: 'wearable-test-003', alert_limits: { status: 'unknown', desired: null, reported: null } }),
    fleetDevice({ device_id: 'wearable-test-004', alert_limits: { status: 'pending', desired: NEW, reported: null } }),
    fleetDevice({ device_id: 'wearable-test-005', alert_limits: null }),
    fleetDevice({ device_id: 'wearable-test-006', alert_limits: undefined }),
    spareDevice({ device_id: 'bedroom-test-001' }),
  ]

  /** What `GET /devices` sends ops: status only. */
  const opsFleet = () => [
    fleetDevice({ device_id: 'wearable-test-001', alert_limits: { status: 'in_sync' } }),
    fleetDevice({ device_id: 'wearable-test-002', alert_limits: { status: 'pending' } }),
    fleetDevice({ device_id: 'wearable-test-003', alert_limits: { status: 'unknown' } }),
    fleetDevice({ device_id: 'wearable-test-005', alert_limits: null }),
    spareDevice({ device_id: 'bedroom-test-001' }),
  ]

  describe.each<[string, AuthUser]>([
    ['nurse', TEST_USERS.nurse],
    ['doctor', TEST_USERS.doctor],
  ])('staff (%s)', (_, user) => {
    it('in_sync: limits in use and "Limits up to date", no New: line', async () => {
      await renderLoaded(fleet(), user)
      const cell = limitsCell('wearable-test-001')
      expect(cell).toHaveTextContent(/^HR 45-130 bpm · SpO₂ ≥ 88 %\.?\s*Limits up to date$/)
      expect(cell).not.toHaveTextContent('New:')
    })

    it('pending: limits in use, "Waiting for device", and the New: line from desired', async () => {
      await renderLoaded(fleet(), user)
      const cell = limitsCell('wearable-test-002')
      expect(within(cell).getByText('HR 45-130 bpm · SpO₂ ≥ 88 %')).toBeInTheDocument()
      expect(within(cell).getByText('Waiting for device')).toBeInTheDocument()
      expect(within(cell).getByText('New: HR 45-110 bpm · SpO₂ ≥ 88 %')).toBeInTheDocument()
    })

    it('pending with reported null: "No limits reported" plus the New: line', async () => {
      await renderLoaded(fleet(), user)
      const cell = limitsCell('wearable-test-004')
      expect(within(cell).getByText('No limits reported')).toBeInTheDocument()
      expect(within(cell).getByText('Waiting for device')).toBeInTheDocument()
      expect(within(cell).getByText('New: HR 45-110 bpm · SpO₂ ≥ 88 %')).toBeInTheDocument()
    })

    it('unknown with reported null: "No limits reported" and "Status unknown", no New: line', async () => {
      await renderLoaded(fleet(), user)
      const cell = limitsCell('wearable-test-003')
      expect(within(cell).getByText('No limits reported')).toBeInTheDocument()
      expect(within(cell).getByText('Status unknown')).toBeInTheDocument()
      expect(cell).not.toHaveTextContent('New:')
    })

    it.each(['wearable-test-005', 'wearable-test-006'])(
      'a wearable with no usable alert_limits (%s) reads "No limits reported" and "Status unknown"',
      async (id) => {
        await renderLoaded(fleet(), user)
        const cell = limitsCell(id)
        expect(within(cell).getByText('No limits reported')).toBeInTheDocument()
        expect(within(cell).getByText('Status unknown')).toBeInTheDocument()
      },
    )

    it('a bedroom sensor shows "—" with a screen-reader text', async () => {
      await renderLoaded(fleet(), user)
      const cell = limitsCell('bedroom-test-001')
      expect(within(cell).getByText('—')).toHaveAttribute('aria-hidden', 'true')
      expect(within(cell).getByText('No alert limits')).toHaveClass('sr-only')
      expect(cell).not.toHaveTextContent(NUMBERS)
      expect(cell).not.toHaveTextContent(/Limits up to date|Waiting|Status unknown/)
    })

    it('a bedroom sensor shows "—" even if limits were attached', async () => {
      await renderLoaded(
        [fleetDevice({ device_id: 'bedroom-test-002', device_type: 'bedroom', alert_limits: { status: 'in_sync', desired: IN_USE, reported: IN_USE } })],
        user,
      )
      expect(within(limitsCell('bedroom-test-002')).getByText('—')).toBeInTheDocument()
      expect(limitsCell('bedroom-test-002')).not.toHaveTextContent(NUMBERS)
    })
  })

  describe('ops (criterion 17)', () => {
    it.each([
      ['wearable-test-001', 'Limits up to date'],
      ['wearable-test-002', 'Waiting for device'],
      ['wearable-test-003', 'Status unknown'],
      ['wearable-test-005', 'Status unknown'],
    ])('%s shows only the status text "%s"', async (id, label) => {
      await renderLoaded(opsFleet(), TEST_USERS.ops)
      expect(limitsCell(id)).toHaveTextContent(new RegExp(`^${label}$`))
    })

    it('never shows numbers, "No limits reported", or a New: line, even if the data has limits', async () => {
      // Defence in depth: the API sends ops status only, but the UI must not show numbers either way.
      await renderLoaded(fleet(), TEST_USERS.ops)
      for (const id of ['wearable-test-001', 'wearable-test-002', 'wearable-test-003', 'wearable-test-004']) {
        const cell = limitsCell(id)
        expect(cell).not.toHaveTextContent(NUMBERS)
        expect(cell).not.toHaveTextContent('New:')
        expect(cell).not.toHaveTextContent('No limits reported')
      }
      expect(table()).not.toHaveTextContent(/HR \d|SpO₂ ≥/)
      expect(screen.getByRole('columnheader', { name: 'Alert limits' })).toBeInTheDocument()
    })

    it('a bedroom sensor shows "—"', async () => {
      await renderLoaded(opsFleet(), TEST_USERS.ops)
      expect(within(limitsCell('bedroom-test-001')).getByText('—')).toBeInTheDocument()
    })
  })

  it('the demo fleet shows every wearable up to date and every bedroom sensor as "—"', async () => {
    await renderLoaded()
    for (const row of bodyRows()) {
      const id = within(row).getByRole('rowheader').textContent ?? ''
      const cell = within(row).getAllByRole('cell').at(-1) as HTMLElement
      if (id.startsWith('wearable-')) expect(cell).toHaveTextContent(/^HR 45-130 bpm · SpO₂ ≥ 88 %\.?\s*Limits up to date$/)
      else expect(within(cell).getByText('—')).toBeInTheDocument()
    }
  })

  it('status is text in the cell, not colour or an icon alone', async () => {
    await renderLoaded(fleet())
    for (const label of ['Limits up to date', 'Waiting for device', 'Status unknown']) {
      const el = within(table()).getAllByText(label)[0]
      expect(el).toBeVisible()
      expect(el).not.toHaveAttribute('aria-hidden')
      expect(el).not.toHaveClass('sr-only')
    }
  })

  it.each<[string, AuthUser, () => FleetDevice[]]>([
    ['staff', TEST_USERS.nurse, fleet],
    ['ops', TEST_USERS.ops, opsFleet],
  ])('has no axe violations with every limits state (%s)', { timeout: 30_000 }, async (_, user, data) => {
    const { container } = await renderLoaded(data(), user)
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('privacy', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('document.title is "SilverCare - Devices", with no personal data', async () => {
    await renderLoaded()
    await waitFor(() => expect(document.title).toBe('SilverCare - Devices'))
    expect(document.title).not.toContain('Test Resident')
  })

  it('keeps the title while loading and on error', async () => {
    deviceFleetLoader.mockRejectedValue(new Error('boom'))
    renderPage()
    await waitFor(() => expect(document.title).toBe('SilverCare - Devices'))
    await screen.findByRole('alert')
    expect(document.title).toBe('SilverCare - Devices')
  })

  it('uses no storage and no console, through loading, filtering, and Show all', async () => {
    localStorage.clear()
    sessionStorage.clear()
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const getItem = vi.spyOn(Storage.prototype, 'getItem')
    const consoleSpies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m))

    const first = await renderLoaded()
    for (const label of ['Offline', 'Low battery', 'Reporting normally', 'All devices']) {
      await first.user.click(tile(label))
    }
    first.unmount()
    const second = await renderLoaded([fleetDevice()])
    await second.user.click(tile('Offline'))
    await second.user.click(screen.getByRole('button', { name: 'Show all devices' }))

    expect(setItem).not.toHaveBeenCalled()
    expect(getItem).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    consoleSpies.forEach((spy) => expect(spy).not.toHaveBeenCalled())
    expect(location()).toBe('/devices')
  })
})

describe('accessibility', { timeout: 30_000 }, () => {
  it('has no axe violations (all devices)', async () => {
    const { container } = await renderLoaded()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations with a filter applied', async () => {
    const { container, user } = await renderLoaded()
    await user.click(tile('Offline'))
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations with an empty filter and Show all devices', async () => {
    const { container, user } = await renderLoaded([fleetDevice()])
    await user.click(tile('Offline'))
    await screen.findByText('All devices are online.')
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations with an empty fleet', async () => {
    const { container } = await renderLoaded([])
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations with an unassigned, never-reported spare', async () => {
    const { container } = await renderLoaded([spareDevice(), fleetDevice()])
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations in the loading state', async () => {
    deviceFleetLoader.mockReturnValue(deferred<FleetDevice[]>().promise)
    const { container } = renderPage()
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations in the error state', async () => {
    deviceFleetLoader.mockRejectedValue(new Error('boom'))
    const { container } = renderPage()
    await screen.findByRole('alert')
    expect(await axeViolations(container)).toEqual([])
  })

  it('has no axe violations on the staff-only notice', async () => {
    const { container } = renderPage(TEST_USERS.caregiver)
    await screen.findByRole('heading', { level: 2, name: 'This page is for care staff.' })
    expect(await axeViolations(container)).toEqual([])
  })

  it.each([
    ['All devices', ['Devices', 'Summary', 'All devices (40)']],
    ['Offline', ['Devices', 'Summary', 'Offline (6)']],
  ] as const)('headings are in order with "%s" chosen (h1 > h2)', async (label, names) => {
    const { user } = await renderLoaded()
    await user.click(tile(label))
    const headings = screen.getAllByRole('heading')
    expect(headings.map((h) => [Number(h.tagName.slice(1)), h.textContent])).toEqual([
      [1, names[0]],
      [2, names[1]],
      [2, names[2]],
    ])
  })

  it('headings are in order in the empty-fleet, loading, and error states', async () => {
    const empty = await renderLoaded([])
    expect(screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))).toEqual([1, 2])
    empty.unmount()

    deviceFleetLoader.mockReturnValueOnce(deferred<FleetDevice[]>().promise)
    const loading = renderPage()
    expect(screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))).toEqual([1])
    loading.unmount()

    deviceFleetLoader.mockRejectedValueOnce(new Error('boom'))
    renderPage()
    await screen.findByRole('alert')
    expect(screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)))).toEqual([1])
  })

  it('has exactly one role=status live region once loaded, and it is polite and atomic', async () => {
    const { container, user } = await renderLoaded()
    expect(screen.getAllByRole('status')).toHaveLength(1)
    const live = container.querySelectorAll('[aria-live]')
    expect(live).toHaveLength(1)
    expect(live[0]).toBe(announcer())
    expect(live[0]).toHaveAttribute('aria-live', 'polite')
    expect(live[0]).toHaveAttribute('aria-atomic', 'true')
    await user.click(tile('Offline'))
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(container.querySelectorAll('[aria-live]')).toHaveLength(1)
  })

  it('state always has text: every row has type, status, battery, and last seen text', async () => {
    await renderLoaded([...createDeviceFleet(new Date()), spareDevice()])
    bodyRows().forEach((row) => {
      const [type, assigned, status, battery, lastSeen, firmware] = within(row).getAllByRole('cell')
      expect(type).toHaveTextContent(/^(Wearable|Bedroom sensor)$/)
      expect(assigned.textContent?.trim()).not.toBe('')
      expect(status).toHaveTextContent(/^(Online|Offline)$/)
      expect(battery).toHaveTextContent(/^(\d+%( · Low)?( · Charging)?|Mains powered|Unknown)$/)
      expect(lastSeen.textContent?.trim()).not.toBe('')
      expect(firmware.textContent?.trim()).not.toBe('')
    })
  })

  it('tiles have visible focus styles and accessible names with their count', async () => {
    await renderLoaded()
    within(tileGroup())
      .getAllByRole('button')
      .forEach((b) => expect(b).toHaveClass('focus-visible:ring-4'))
    expect(tile('Offline')).toHaveAccessibleName(/^Offline\s?6\s?No status for 15 min$/)
  })
})
