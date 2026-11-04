import type { ResidentDetail } from '../../types/resident'
import {
  DEMO_OFFLINE_TIMEOUT_MIN,
  createDemoData,
  createDeviceFleet,
  createResidentDetail,
  residentId,
} from './demoData'

const NOW = new Date('2026-10-18T02:30:00Z')
const fleet = createDeviceFleet(NOW)
const { residents } = createDemoData(NOW)
const ageMin = (iso: string | null) => (iso === null ? null : (NOW.getTime() - Date.parse(iso)) / 60_000)

const deviceOf = (n: number, type: 'wearable' | 'bedroom') => {
  const d = fleet.find((f) => f.device_type === type && f.assigned_to?.resident_id === residentId(n))
  if (!d) throw new Error(`No ${type} for resident ${n}`)
  return d
}

describe('createDeviceFleet (demo)', () => {
  it('has 40 devices: one wearable and one bedroom sensor per resident', () => {
    expect(fleet).toHaveLength(40)
    expect(fleet.filter((d) => d.device_type === 'wearable')).toHaveLength(20)
    expect(fleet.filter((d) => d.device_type === 'bedroom')).toHaveLength(20)
    residents.forEach((r) => {
      const mine = fleet.filter((d) => d.assigned_to?.resident_id === r.resident_id)
      expect(mine.map((d) => d.device_type).sort()).toEqual(['bedroom', 'wearable'])
    })
  })

  it('every device ID is unique and opaque', () => {
    const ids = fleet.map((d) => d.device_id)
    expect(new Set(ids).size).toBe(40)
    fleet.forEach((d) => expect(d.device_id).toMatch(d.device_type === 'wearable' ? /^wearable-\d{4}$/ : /^bedroom-\d{4}$/))
  })

  it('assignments carry only the resident ID, a synthetic name, and the home ID', () => {
    fleet.forEach((d) => {
      expect(d.assigned_to).not.toBeNull()
      expect(Object.keys(d.assigned_to ?? {}).sort()).toEqual(['home_id', 'name', 'resident_id'])
      expect(d.assigned_to?.name).toMatch(/^Test Resident [A-T]$/)
      expect(d.assigned_to?.resident_id).toMatch(/^res-\d{4}$/)
      expect(d.assigned_to?.home_id).toMatch(/^home-\d{4}$/)
    })
  })

  it('agrees with createDemoData: IDs, online, last_seen = ts, wearable battery and charging', () => {
    residents.forEach((r, i) => {
      const w = deviceOf(i + 1, 'wearable')
      const b = deviceOf(i + 1, 'bedroom')
      expect(w.assigned_to).toEqual({ resident_id: r.resident_id, name: r.name, home_id: r.home_id })
      expect(w.device_id).toBe(r.wearable.device_id)
      expect(w.online).toBe(r.wearable.online)
      expect(w.last_seen).toBe(r.wearable.ts)
      expect(w.battery_pct).toBe(r.wearable.battery_pct)
      expect(w.charging).toBe(r.wearable.charging)
      expect(b.device_id).toBe(r.bedroom.device_id)
      expect(b.online).toBe(r.bedroom.online)
      expect(b.last_seen).toBe(r.bedroom.ts)
    })
  })

  it("agrees with createResidentDetail: online, newest status time, battery, and firmware", () => {
    residents.forEach((r, i) => {
      const detail = createResidentDetail(r.resident_id, NOW) as ResidentDetail
      const w = deviceOf(i + 1, 'wearable')
      const b = deviceOf(i + 1, 'bedroom')
      const ws = detail.wearable?.latest.status[0]
      const bs = detail.bedroom?.latest.status[0]
      expect(w.online).toBe(detail.wearable?.online)
      expect(w.last_seen).toBe(ws?.ts)
      expect(w.battery_pct).toBe(ws?.battery_pct)
      expect(w.charging).toBe(ws?.charging)
      expect(w.fw_version).toBe(ws?.fw_version)
      expect(b.online).toBe(detail.bedroom?.online)
      expect(b.last_seen).toBe(bs?.ts)
      expect(b.battery_pct).toBe(bs?.battery_pct)
      expect(b.fw_version).toBe(bs?.fw_version)
    })
  })

  it('a device is online exactly when its last status is less than 15 minutes old', () => {
    expect(DEMO_OFFLINE_TIMEOUT_MIN).toBe(15)
    fleet.forEach((d) => {
      const age = ageMin(d.last_seen) as number
      expect(d.online).toBe(age < DEMO_OFFLINE_TIMEOUT_MIN)
    })
  })

  it('has exactly the 6 offline devices, with their minutes since last seen', () => {
    const offline = fleet
      .filter((d) => !d.online)
      .map((d) => [d.assigned_to?.resident_id, d.device_type, ageMin(d.last_seen)])
    expect(offline).toEqual([
      ['res-0006', 'bedroom', 26 * 60],
      ['res-0009', 'bedroom', 70],
      ['res-0012', 'wearable', 18],
      ['res-0015', 'wearable', 52],
      ['res-0015', 'bedroom', 185],
      ['res-0017', 'bedroom', 2 * 24 * 60],
    ])
  })

  it('res-0009 has an offline bedroom sensor but a working wearable', () => {
    expect(deviceOf(9, 'bedroom').online).toBe(false)
    expect(deviceOf(9, 'wearable').online).toBe(true)
  })

  it('the offline wearables have vitals as old as their last status', () => {
    for (const n of [12, 15]) {
      const r = residents[n - 1]
      expect(r.vitals[0].ts).toBe(r.wearable.ts)
    }
  })

  it('online devices were seen 2 (wearable) or 3 (bedroom) minutes ago', () => {
    fleet
      .filter((d) => d.online)
      .forEach((d) => expect(ageMin(d.last_seen)).toBe(d.device_type === 'wearable' ? 2 : 3))
  })

  it('low battery (<20 %): wearables res-0006 15 % and res-0013 12 %, bedroom res-0004 9 %', () => {
    const low = fleet
      .filter((d) => d.battery_pct !== null && d.battery_pct < 20)
      .map((d) => [d.assigned_to?.resident_id, d.device_type, d.battery_pct])
    expect(low).toEqual([
      ['res-0004', 'bedroom', 9],
      ['res-0006', 'wearable', 15],
      ['res-0013', 'wearable', 12],
    ])
  })

  it('every fourth bedroom sensor is mains-powered (null battery), except where the seed overrides it', () => {
    const mains = fleet.filter((d) => d.battery_pct === null).map((d) => [d.assigned_to?.resident_id, d.device_type])
    expect(mains).toEqual([
      ['res-0001', 'bedroom'],
      ['res-0005', 'bedroom'],
      ['res-0009', 'bedroom'],
      ['res-0013', 'bedroom'],
      ['res-0017', 'bedroom'],
    ])
  })

  it('other bedroom batteries follow 60 + ((i * 7) % 35)', () => {
    residents.forEach((_, i) => {
      if (i % 4 === 0 || i === 3) return
      expect(deviceOf(i + 1, 'bedroom').battery_pct).toBe(60 + ((i * 7) % 35))
    })
  })

  it('bedroom sensors never charge; every device reports firmware 1.0.0', () => {
    fleet.forEach((d) => {
      if (d.device_type === 'bedroom') expect(d.charging).toBe(false)
      expect(d.fw_version).toBe('1.0.0')
    })
  })

  it('only res-0010 has a charging wearable', () => {
    expect(fleet.filter((d) => d.charging).map((d) => d.assigned_to?.resident_id)).toEqual(['res-0010'])
  })

  it('last_seen is ISO 8601 UTC', () => {
    fleet.forEach((d) => expect(d.last_seen).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/))
  })

  it('is deterministic for the same now', () => {
    expect(createDeviceFleet(NOW)).toEqual(fleet)
  })
})
