import { apiAlert, apiResident } from '../../test/apiDashboard'
import type { Home, Resident } from '../../types/dashboard'
import { groupAlertsByHome } from './alertsMap'
import { toResident, toResidentAlert } from './useDashboardData'

const HOME_1: Home = { home_id: 'home-test-001', address: 'Test address 1 (synthetic)', lat: 1.36, lng: 103.8 }
const HOME_2: Home = { home_id: 'home-test-002', address: 'Test address 2 (synthetic)', lat: 1.37, lng: 103.81 }

function residents(...list: Resident[]): Map<string, Resident> {
  return new Map(list.map((r) => [r.resident_id, r]))
}

const resA = toResident(apiResident(1)) // home-test-001
const resB = toResident(apiResident(2)) // home-test-002
const resC = toResident(apiResident(3, { home_id: 'home-test-001' })) // shares home 1
const resNoHome = toResident(apiResident(4, { home_id: null }))

const alert = (id: number, resident_id: string, severity: 'critical' | 'warning', extra = {}) =>
  toResidentAlert(apiAlert({ alert_id: `alert-test-${id}`, resident_id, severity, ...extra }))

describe('groupAlertsByHome', () => {
  it('returns nothing for no alerts', () => {
    expect(groupAlertsByHome([], residents(resA), [HOME_1])).toEqual({ pins: [], missing: [] })
  })

  it('makes one pin per home, with residents of the same home grouped together', () => {
    const open = [
      alert(1, resA.resident_id, 'warning'),
      alert(2, resC.resident_id, 'warning'),
      alert(3, resA.resident_id, 'warning'),
      alert(4, resB.resident_id, 'warning'),
    ]
    const { pins, missing } = groupAlertsByHome(open, residents(resA, resB, resC), [HOME_1, HOME_2])
    expect(missing).toEqual([])
    expect(pins.map((p) => p.home.home_id)).toEqual(['home-test-001', 'home-test-002'])
    expect(pins[0].residents.map((r) => r.resident_id)).toEqual([resA.resident_id, resC.resident_id])
    expect(pins[0].residents[0].alerts.map((a) => a.alert_id)).toEqual(['alert-test-1', 'alert-test-3'])
    expect(pins[0].residents[0].resident).toBe(resA)
    expect(pins[1].residents).toHaveLength(1)
  })

  it('takes the worst severity: critical if any alert at the home is critical, else warning', () => {
    const open = [
      alert(1, resA.resident_id, 'warning'),
      alert(2, resC.resident_id, 'critical'),
      alert(3, resA.resident_id, 'warning'),
      alert(4, resB.resident_id, 'warning'),
    ]
    const { pins } = groupAlertsByHome(open, residents(resA, resB, resC), [HOME_1, HOME_2])
    expect(pins.map((p) => p.severity)).toEqual(['critical', 'warning'])
  })

  it('a critical alert first then a warning still gives critical', () => {
    const open = [alert(1, resA.resident_id, 'critical'), alert(2, resA.resident_id, 'warning')]
    expect(groupAlertsByHome(open, residents(resA), [HOME_1]).pins[0].severity).toBe('critical')
  })

  it("keeps the server's home order, not the alert order", () => {
    const open = [alert(1, resB.resident_id, 'critical'), alert(2, resA.resident_id, 'warning')]
    const { pins } = groupAlertsByHome(open, residents(resA, resB), [HOME_1, HOME_2])
    expect(pins.map((p) => p.home.home_id)).toEqual(['home-test-001', 'home-test-002'])
  })

  it('a resident whose home is not in homes gets one "no location on file" line, however many alerts', () => {
    const open = [alert(1, resB.resident_id, 'warning'), alert(2, resB.resident_id, 'critical')]
    const { pins, missing } = groupAlertsByHome(open, residents(resB), [HOME_1])
    expect(pins).toEqual([])
    expect(missing).toEqual(['Test Resident B (home-test-002): no location on file'])
  })

  it('a resident with no home_id is listed as "(no home ID)"', () => {
    const { missing } = groupAlertsByHome([alert(1, resNoHome.resident_id, 'warning')], residents(resNoHome), [HOME_1])
    expect(missing).toEqual(['Test Resident D (no home ID): no location on file'])
  })

  it('an alert for a resident not in the residents list is listed as an unknown resident', () => {
    const { missing } = groupAlertsByHome([alert(1, 'resident-test-099', 'warning')], residents(), [HOME_1])
    expect(missing).toEqual(['Unknown resident (no home ID): no location on file'])
  })

  it('unassigned devices are listed once per device, never pinned', () => {
    const open = [
      alert(1, 'unassigned', 'warning', { device_id: 'wearable-test-050', alert_type: 'battery_low' }),
      alert(2, 'unassigned', 'warning', { device_id: 'wearable-test-050', alert_type: 'device_offline' }),
      alert(3, 'unassigned', 'critical', { device_id: 'bedroom-test-051' }),
    ]
    const { pins, missing } = groupAlertsByHome(open, residents(resA), [HOME_1])
    expect(pins).toEqual([])
    expect(missing).toEqual([
      'Unassigned device wearable-test-050: no home',
      'Unassigned device bedroom-test-051: no home',
    ])
  })

  it('with homes empty (homes_error) every alert is listed under the map', () => {
    const open = [alert(1, resA.resident_id, 'critical'), alert(2, resB.resident_id, 'warning')]
    const { pins, missing } = groupAlertsByHome(open, residents(resA, resB), [])
    expect(pins).toEqual([])
    expect(missing).toEqual([
      'Test Resident A (home-test-001): no location on file',
      'Test Resident B (home-test-002): no location on file',
    ])
  })

  it('a home in homes with no open alert gets no pin', () => {
    const { pins } = groupAlertsByHome([alert(1, resA.resident_id, 'warning')], residents(resA), [HOME_1, HOME_2])
    expect(pins.map((p) => p.home.home_id)).toEqual(['home-test-001'])
  })
})
