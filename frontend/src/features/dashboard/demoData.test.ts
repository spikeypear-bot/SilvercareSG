import { FAMILY_LINKED_RESIDENT_IDS, STATUS_TEXT, createDemoData } from './demoData'

const NOW = new Date('2026-10-18T02:30:00Z')
const data = createDemoData(NOW)

const isIntOrNull = (v: number | null) => v === null || Number.isInteger(v)
const inRange = (v: number | null, min: number, max: number) => v === null || (v >= min && v <= max)

describe('demo residents (criteria 18, 21)', () => {
  it('has 20 residents with synthetic names and IDs', () => {
    expect(data.residents).toHaveLength(20)
    data.residents.forEach((r, i) => {
      expect(r.name).toBe(`Test Resident ${'ABCDEFGHIJKLMNOPQRST'[i]}`)
      expect(r.resident_id).toBe(`res-${String(i + 1).padStart(4, '0')}`)
      expect(r.wearable.device_id).toMatch(/^wearable-\d{4}$/)
      expect(r.bedroom.device_id).toMatch(/^bedroom-\d{4}$/)
    })
  })

  it('gives every device a unique ID', () => {
    const ids = data.residents.flatMap((r) => [r.wearable.device_id, r.bedroom.device_id])
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('links the same 2 existing residents to every family account', () => {
    expect(FAMILY_LINKED_RESIDENT_IDS).toHaveLength(2)
    const ids = data.residents.map((r) => r.resident_id)
    FAMILY_LINKED_RESIDENT_IDS.forEach((id) => expect(ids).toContain(id))
  })
})

describe('shapes and units follow docs/metrics.md (criterion 18)', () => {
  it('keeps the latest 5 vitals per resident, newest first, with metrics-contract fields', () => {
    data.residents.forEach((r) => {
      expect(r.vitals).toHaveLength(5)
      const times = r.vitals.map((v) => v.ts)
      expect([...times].sort().reverse()).toEqual(times)
      r.vitals.forEach((v) => {
        expect(Object.keys(v).sort()).toEqual(
          ['activity_pct', 'hr_bpm', 'skin_temp_c', 'spo2_pct', 'ts'].sort(),
        )
        expect(isIntOrNull(v.hr_bpm) && inRange(v.hr_bpm, 20, 250)).toBe(true)
        expect(isIntOrNull(v.spo2_pct) && inRange(v.spo2_pct, 50, 100)).toBe(true)
        expect(inRange(v.skin_temp_c, 20, 45)).toBe(true)
        if (v.skin_temp_c !== null) {
          expect(Math.round(v.skin_temp_c * 10) / 10).toBe(v.skin_temp_c)
        }
        expect(Number.isInteger(v.activity_pct) && inRange(v.activity_pct, 0, 100)).toBe(true)
        expect(new Date(v.ts).getTime()).toBeLessThanOrEqual(NOW.getTime())
      })
    })
  })

  it('wearable status uses battery_pct, worn, and charging', () => {
    data.residents.forEach((r) => {
      expect(Number.isInteger(r.wearable.battery_pct)).toBe(true)
      expect(r.wearable.battery_pct).toBeGreaterThanOrEqual(0)
      expect(r.wearable.battery_pct).toBeLessThanOrEqual(100)
      expect(typeof r.wearable.worn).toBe('boolean')
      expect(typeof r.wearable.charging).toBe('boolean')
    })
  })

  it('timestamps are ISO 8601 UTC', () => {
    const all = data.residents.flatMap((r) => [r.wearable.ts, r.bedroom.ts, ...r.vitals.map((v) => v.ts)])
    all.concat(data.alerts.map((a) => a.event_ts)).forEach((ts) => {
      expect(ts).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/)
    })
  })
})

describe('status and severity are provided by the data (criterion 19)', () => {
  it('every resident carries a status and the matching plain sentence', () => {
    data.residents.forEach((r) => {
      expect(['ok', 'attention', 'urgent']).toContain(r.status)
      expect(r.status_text).toBe(STATUS_TEXT[r.status])
    })
    expect(STATUS_TEXT).toEqual({
      ok: 'All readings look normal',
      attention: 'Needs attention',
      urgent: 'Urgent: check now',
    })
  })

  it('every alert is open, has a severity, and points at its resident and device', () => {
    expect(data.alerts.length).toBeGreaterThan(0)
    const byId = new Map(data.residents.map((r) => [r.resident_id, r]))
    data.alerts.forEach((a) => {
      expect(a.status).toBe('open')
      expect(['info', 'warning', 'critical']).toContain(a.severity)
      const resident = byId.get(a.resident_id)
      expect(resident).toBeDefined()
      expect([resident?.wearable.device_id, resident?.bedroom.device_id]).toContain(a.device_id)
    })
    expect(new Set(data.alerts.map((a) => a.alert_id)).size).toBe(data.alerts.length)
  })

  it('alerts are newest first', () => {
    const times = data.alerts.map((a) => a.event_ts)
    expect([...times].sort().reverse()).toEqual(times)
  })

  it('includes every status and severity so each badge can be seen', () => {
    expect(new Set(data.residents.map((r) => r.status))).toEqual(new Set(['ok', 'attention', 'urgent']))
    expect(new Set(data.alerts.map((a) => a.severity))).toEqual(new Set(['info', 'warning', 'critical']))
  })
})
