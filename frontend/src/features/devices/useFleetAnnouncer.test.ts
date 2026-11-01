import { act, renderHook } from '@testing-library/react'
import { createDeviceFleet } from '../dashboard/demoData'
import { fleetDevice, minutesAgo } from '../../test/fleetMock'
import type { FleetDevice } from '../../types/devices'
import { useFleetAnnouncer } from './useFleetAnnouncer'

const NOW = new Date('2026-10-18T02:30:00Z')

/** The message without the alternating trailing no-break space. */
const msg = (result: { current: { message: string } }) => result.current.message.replace(/ $/, '')

const wearable7 = fleetDevice({ device_id: 'wearable-0007', device_type: 'wearable' }, NOW)
const bedroom3 = fleetDevice({ device_id: 'bedroom-0003', device_type: 'bedroom', battery_pct: null }, NOW)
const wearable9 = fleetDevice({ device_id: 'wearable-0009', device_type: 'wearable' }, NOW)
const offline = (d: FleetDevice): FleetDevice => ({ ...d, online: false, last_seen: minutesAgo(20, NOW) })
const online = (d: FleetDevice): FleetDevice => ({ ...d, online: true, last_seen: minutesAgo(1, NOW) })

function setup(initial: FleetDevice[]) {
  return renderHook(({ devices }) => useFleetAnnouncer(devices), { initialProps: { devices: initial } })
}

describe('useFleetAnnouncer', () => {
  it('is quiet on first load, even with offline devices', () => {
    const fleet = createDeviceFleet(NOW)
    expect(fleet.some((d) => !d.online)).toBe(true)
    const { result } = setup(fleet)
    expect(result.current.message).toBe('')
  })

  it('is quiet when the same devices arrive again in a new array', () => {
    const fleet = createDeviceFleet(NOW)
    const { result, rerender } = setup(fleet)
    rerender({ devices: createDeviceFleet(NOW) })
    expect(result.current.message).toBe('')
  })

  it('announces one wearable that went offline', () => {
    const { result, rerender } = setup([wearable7, bedroom3])
    rerender({ devices: [offline(wearable7), bedroom3] })
    expect(msg(result)).toBe('Wearable wearable-0007 went offline.')
  })

  it('announces one bedroom sensor that went offline', () => {
    const { result, rerender } = setup([wearable7, bedroom3])
    rerender({ devices: [wearable7, offline(bedroom3)] })
    expect(msg(result)).toBe('Bedroom sensor bedroom-0003 went offline.')
  })

  it('announces one device that is back online', () => {
    const { result, rerender } = setup([offline(wearable7), bedroom3])
    rerender({ devices: [online(wearable7), bedroom3] })
    expect(msg(result)).toBe('Wearable wearable-0007 is back online.')
  })

  it('counts several devices that went offline at once', () => {
    const { result, rerender } = setup([wearable7, bedroom3, wearable9])
    rerender({ devices: [offline(wearable7), offline(bedroom3), offline(wearable9)] })
    expect(msg(result)).toBe('3 devices went offline.')
  })

  it('counts several devices that are back online at once', () => {
    const { result, rerender } = setup([offline(wearable7), offline(bedroom3)])
    rerender({ devices: [online(wearable7), online(bedroom3)] })
    expect(msg(result)).toBe('2 devices are back online.')
  })

  it('says both when some go offline and others come back in the same update', () => {
    const { result, rerender } = setup([wearable7, offline(bedroom3)])
    rerender({ devices: [offline(wearable7), online(bedroom3)] })
    expect(msg(result)).toBe('Wearable wearable-0007 went offline. Bedroom sensor bedroom-0003 is back online.')

    const { result: r2, rerender: rr2 } = setup([wearable7, wearable9, offline(bedroom3)])
    rr2({ devices: [offline(wearable7), offline(wearable9), online(bedroom3)] })
    expect(msg(r2)).toBe('2 devices went offline. Bedroom sensor bedroom-0003 is back online.')
  })

  it('does not announce a device that is new in this update, online or offline', () => {
    const { result, rerender } = setup([wearable7])
    rerender({ devices: [wearable7, offline(bedroom3), wearable9] })
    expect(result.current.message).toBe('')
  })

  it('does not announce battery or last_seen changes', () => {
    const { result, rerender } = setup([wearable7])
    rerender({ devices: [{ ...wearable7, battery_pct: 5, last_seen: minutesAgo(0, NOW) }] })
    expect(result.current.message).toBe('')
  })

  it('compares with the previous update, so a change is announced only once', () => {
    const { result, rerender } = setup([wearable7])
    const next = [offline(wearable7)]
    rerender({ devices: next })
    expect(msg(result)).toBe('Wearable wearable-0007 went offline.')
    act(() => result.current.announce('Showing 1 of 1 devices: offline.'))
    rerender({ devices: [...next] })
    expect(msg(result)).toBe('Showing 1 of 1 devices: offline.')
  })

  it('announce() sets the message directly, and repeats are re-read', () => {
    const { result } = setup([wearable7])
    act(() => result.current.announce('Showing 6 of 40 devices: offline.'))
    const first = result.current.message
    act(() => result.current.announce('Showing 6 of 40 devices: offline.'))
    expect(result.current.message).not.toBe(first)
    expect(msg(result)).toBe('Showing 6 of 40 devices: offline.')
  })
})
