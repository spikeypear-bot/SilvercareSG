import { demoDataFor } from '../../test/dashboardMock'
import type { ResidentAlert } from '../../types/dashboard'
import {
  dashboardStats,
  lowBatteryResidents,
  offlineDevices,
  sortAlertsForInbox,
  unresolvedAlerts,
} from './stats'

const NOW = new Date('2026-10-18T02:30:00Z')
const data = demoDataFor('staff', NOW)

function alert(id: string, severity: ResidentAlert['severity'], minutesAgo: number, status: ResidentAlert['status'] = 'open'): ResidentAlert {
  return {
    alert_id: id,
    resident_id: 'res-0001',
    device_id: 'wearable-test-001',
    alert_type: 'vitals',
    severity,
    status,
    event_ts: new Date(NOW.getTime() - minutesAgo * 60_000).toISOString(),
  }
}

describe('unresolvedAlerts', () => {
  it('keeps open and acknowledged alerts, drops resolved ones', () => {
    const alerts = [alert('a', 'info', 1), alert('b', 'info', 2, 'acknowledged'), alert('c', 'info', 3, 'resolved')]
    expect(unresolvedAlerts(alerts).map((a) => a.alert_id)).toEqual(['a', 'b'])
  })
})

describe('sortAlertsForInbox', () => {
  it('puts critical first, then newest first within each group', () => {
    const alerts = [
      alert('warn-new', 'warning', 1),
      alert('crit-old', 'critical', 30),
      alert('info-mid', 'info', 10),
      alert('crit-new', 'critical', 5),
      alert('warn-old', 'warning', 60),
    ]
    expect(sortAlertsForInbox(alerts).map((a) => a.alert_id)).toEqual([
      'crit-new',
      'crit-old',
      'warn-new',
      'info-mid',
      'warn-old',
    ])
  })

  it('does not change the input array', () => {
    const alerts = [alert('a', 'info', 1), alert('b', 'critical', 2)]
    sortAlertsForInbox(alerts)
    expect(alerts.map((a) => a.alert_id)).toEqual(['a', 'b'])
  })
})

describe('offlineDevices and lowBatteryResidents', () => {
  it('lists each offline wearable and bedroom sensor', () => {
    const devices = offlineDevices(data.residents)
    expect(devices.map((d) => [d.resident.resident_id, d.kind])).toEqual([
      ['res-0009', 'bedroom'],
      ['res-0015', 'wearable'],
    ])
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
      devicesOffline: 2,
      lowBattery: 2,
    })
  })

  it('leaves resolved alerts out of the open and critical counts', () => {
    const alerts = data.alerts.map((a) => (a.severity === 'critical' ? { ...a, status: 'resolved' as const } : a))
    expect(dashboardStats({ ...data, alerts })).toMatchObject({ openAlerts: 9, criticalAlerts: 0 })
  })
})
