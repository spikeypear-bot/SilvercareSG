import { demoDataFor } from '../../test/dashboardMock'
import type { ResidentAlert } from '../../types/dashboard'
import {
  dashboardStats,
  isOnOpenList,
  lowBatteryResidents,
  offlineDevices,
  openListAlerts,
  sortAlertsForInbox,
} from './stats'

const NOW = new Date('2026-10-18T02:30:00Z')
const data = demoDataFor('staff', NOW)

function alert(id: string, severity: ResidentAlert['severity'], minutesAgo: number, status: ResidentAlert['status'] = 'open'): ResidentAlert {
  return {
    alert_id: id,
    resident_id: 'res-0001',
    device_id: 'wearable-test-001',
    alert_type: 'hr_high',
    severity,
    status,
    event_ts: new Date(NOW.getTime() - minutesAgo * 60_000).toISOString(),
  }
}

describe('isOnOpenList', () => {
  it.each([
    ['open', true],
    ['acknowledged', true],
    ['cancelled', false],
    ['resolved', false],
  ] as const)('%s -> %s', (status, expected) => {
    expect(isOnOpenList(alert('a', 'warning', 1, status))).toBe(expected)
  })

  it('looks only at the status, not at by/at fields left on the alert', () => {
    const cancelled: ResidentAlert = {
      ...alert('a', 'critical', 1, 'cancelled'),
      cancelled_at: NOW.toISOString(),
      cancelled_by_role: 'resident',
    }
    expect(isOnOpenList(cancelled)).toBe(false)
    const ackedWithoutBy = alert('b', 'warning', 1, 'acknowledged')
    expect(isOnOpenList(ackedWithoutBy)).toBe(true)
  })
})

describe('openListAlerts', () => {
  it('keeps open and acknowledged alerts, drops cancelled and resolved ones, in order', () => {
    const alerts = [
      alert('a', 'warning', 1),
      alert('b', 'warning', 2, 'acknowledged'),
      alert('c', 'warning', 3, 'resolved'),
      alert('d', 'critical', 4, 'cancelled'),
      alert('e', 'critical', 5),
    ]
    expect(openListAlerts(alerts).map((a) => a.alert_id)).toEqual(['a', 'b', 'e'])
  })

  it('returns an empty list when every alert is cancelled or resolved', () => {
    expect(openListAlerts([alert('c', 'warning', 3, 'resolved'), alert('d', 'critical', 4, 'cancelled')])).toEqual([])
    expect(openListAlerts([])).toEqual([])
  })

  it('does not change the input array', () => {
    const alerts = [alert('a', 'warning', 1), alert('d', 'critical', 4, 'cancelled')]
    openListAlerts(alerts)
    expect(alerts.map((a) => a.alert_id)).toEqual(['a', 'd'])
  })
})

describe('sortAlertsForInbox', () => {
  it('puts critical first, then newest first within each group', () => {
    const alerts = [
      alert('warn-new', 'warning', 1),
      alert('crit-old', 'critical', 30),
      alert('warn-mid', 'warning', 10),
      alert('crit-new', 'critical', 5),
      alert('warn-old', 'warning', 60),
    ]
    expect(sortAlertsForInbox(alerts).map((a) => a.alert_id)).toEqual([
      'crit-new',
      'crit-old',
      'warn-new',
      'warn-mid',
      'warn-old',
    ])
  })

  it('does not change the input array', () => {
    const alerts = [alert('a', 'warning', 1), alert('b', 'critical', 2)]
    sortAlertsForInbox(alerts)
    expect(alerts.map((a) => a.alert_id)).toEqual(['a', 'b'])
  })
})

describe('offlineDevices and lowBatteryResidents', () => {
  it('lists each offline wearable and bedroom sensor', () => {
    const devices = offlineDevices(data.residents)
    // Resident order, wearable before bedroom. O (res-0015) has both devices offline.
    expect(devices.map((d) => [d.resident.resident_id, d.kind])).toEqual([
      ['res-0006', 'bedroom'],
      ['res-0009', 'bedroom'],
      ['res-0012', 'wearable'],
      ['res-0015', 'wearable'],
      ['res-0015', 'bedroom'],
      ['res-0017', 'bedroom'],
    ])
    expect(devices.every((d) => d.device.online === false)).toBe(true)
  })

  it('lists wearables below 20 %', () => {
    expect(lowBatteryResidents(data.residents).map((r) => r.resident_id)).toEqual(['res-0006', 'res-0013'])
    const at20 = data.residents.map((r) => ({ ...r, wearable: { ...r.wearable, battery_pct: 20 } }))
    expect(lowBatteryResidents(at20)).toEqual([])
  })
})

describe('dashboardStats', () => {
  it('counts the demo data', () => {
    expect(dashboardStats(data)).toEqual({
      residents: 20,
      openAlerts: 12,
      criticalAlerts: 3,
      devicesOffline: 6,
      // Wearables only: res-0004's low bedroom sensor is not counted here.
      lowBattery: 2,
    })
  })

  it('leaves resolved alerts out of the open and critical counts', () => {
    const alerts = data.alerts.map((a) => (a.severity === 'critical' ? { ...a, status: 'resolved' as const } : a))
    expect(dashboardStats({ ...data, alerts })).toMatchObject({ openAlerts: 9, criticalAlerts: 0 })
  })

  it('leaves cancelled alerts out of the open and critical counts', () => {
    const alerts = data.alerts.map((a) =>
      a.severity === 'critical'
        ? { ...a, status: 'cancelled' as const, cancelled_at: NOW.toISOString(), cancelled_by_role: 'resident' as const }
        : a,
    )
    expect(dashboardStats({ ...data, alerts })).toMatchObject({ openAlerts: 9, criticalAlerts: 0 })
  })

  it('counts acknowledged alerts as open', () => {
    const alerts = [alert('a', 'critical', 1, 'acknowledged'), alert('b', 'warning', 2, 'cancelled')]
    expect(dashboardStats({ ...data, alerts })).toMatchObject({ openAlerts: 1, criticalAlerts: 1 })
  })
})
