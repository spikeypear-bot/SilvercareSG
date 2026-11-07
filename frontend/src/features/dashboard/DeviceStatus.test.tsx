/**
 * Device state with missing devices and never-reported values (GET /dashboard sends null
 * for both). Every state is text, never an aria-hidden icon alone.
 */
import { render } from '@testing-library/react'
import type { BedroomSensorState, WearableState } from '../../types/dashboard'
import { BatteryLevel, BedroomStatusText, WearableStatusText } from './DeviceStatus'

function wearable(overrides: Partial<WearableState> = {}): WearableState {
  return {
    device_id: 'wearable-test-001',
    ts: '2026-11-07T02:00:00Z',
    battery_pct: 64,
    worn: true,
    charging: false,
    online: true,
    ...overrides,
  }
}

function bedroom(overrides: Partial<BedroomSensorState> = {}): BedroomSensorState {
  return { device_id: 'bedroom-test-001', ts: '2026-11-07T02:00:00Z', online: true, ...overrides }
}

/** Icons in the output are decorative only. */
function expectIconsHidden(container: HTMLElement) {
  container.querySelectorAll('svg').forEach((svg) => expect(svg).toHaveAttribute('aria-hidden', 'true'))
}

describe('BatteryLevel', () => {
  it('shows "Unknown", with no battery icon, for no wearable', () => {
    const { container } = render(<BatteryLevel wearable={null} />)
    expect(container).toHaveTextContent(/^Unknown$/)
    expect(container.querySelector('svg')).toBeNull()
  })

  it('shows "Unknown" for a wearable that has never reported a level', () => {
    const { container } = render(<BatteryLevel wearable={wearable({ battery_pct: null })} />)
    expect(container).toHaveTextContent(/^Unknown$/)
    expect(container.querySelector('svg')).toBeNull()
  })

  it('shows "Unknown" for a null level even while charging', () => {
    const { container } = render(<BatteryLevel wearable={wearable({ battery_pct: null, charging: true })} />)
    expect(container).toHaveTextContent(/^Unknown$/)
  })

  it.each([0, 12, 64, 100])('shows a known level %i as "<n>%" with a hidden icon', (pct) => {
    const { container } = render(<BatteryLevel wearable={wearable({ battery_pct: pct })} />)
    expect(container).toHaveTextContent(new RegExp(`^${pct}%$`))
    expect(container.querySelector('svg')).not.toBeNull()
    expectIconsHidden(container)
  })
})

describe('WearableStatusText', () => {
  it('shows "No wearable assigned" for no wearable', () => {
    const { container } = render(<WearableStatusText wearable={null} />)
    expect(container).toHaveTextContent(/^No wearable assigned$/)
    expectIconsHidden(container)
  })

  it('shows "Offline · Never reported" for an offline wearable that has never reported', () => {
    const { container } = render(
      <WearableStatusText wearable={wearable({ ts: null, battery_pct: null, worn: null, online: false })} />,
    )
    expect(container).toHaveTextContent(/^Offline · Never reported$/)
    expectIconsHidden(container)
  })

  it('shows "Never reported" (not Worn / Not worn) when ts is null, even if worn is set', () => {
    const { container } = render(<WearableStatusText wearable={wearable({ ts: null, worn: true })} />)
    expect(container).toHaveTextContent(/^Never reported$/)
    expect(container).not.toHaveTextContent(/Worn/i)
  })

  it.each([
    [{}, 'Worn'],
    [{ worn: false }, 'Not worn'],
    [{ online: false }, 'Offline · Worn'],
    [{ charging: true, worn: false }, 'Not worn · Charging'],
    [{ worn: null, online: false }, 'Offline · Worn status unknown'],
    [{ worn: null, charging: true }, 'Worn status unknown · Charging'],
  ] as const)('a reported wearable %j reads "%s"', (overrides, text) => {
    const { container } = render(<WearableStatusText wearable={wearable(overrides)} />)
    expect(container).toHaveTextContent(new RegExp(`^${text}$`))
  })

  // An online wearable that reported but with no valid `worn` value (the type allows it:
  // "null when the device has never reported, or sent an invalid value").
  it('shows some text (not an icon alone) for an online wearable with an unknown worn state', () => {
    const { container } = render(<WearableStatusText wearable={wearable({ worn: null })} />)
    expect(container.textContent?.trim()).not.toBe('')
  })
})

describe('BedroomStatusText', () => {
  it('shows "No bedroom sensor assigned" for no bedroom unit', () => {
    const { container } = render(<BedroomStatusText bedroom={null} />)
    expect(container).toHaveTextContent(/^No bedroom sensor assigned$/)
    expectIconsHidden(container)
  })

  it.each([
    [{}, 'Online'],
    [{ online: false }, 'Offline'],
    [{ online: false, ts: null }, 'Offline'],
  ] as const)('a bedroom unit %j reads "%s"', (overrides, text) => {
    const { container } = render(<BedroomStatusText bedroom={bedroom(overrides)} />)
    expect(container).toHaveTextContent(new RegExp(`^${text}$`))
    expectIconsHidden(container)
  })
})
