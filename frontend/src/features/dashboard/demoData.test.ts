import {
  DEMO_CONFIRMATION_WINDOW_MS,
  FAMILY_LINKED_RESIDENT_IDS,
  STATUS_TEXT,
  createDemoData,
} from './demoData'

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

  it('gives every resident a unique opaque home ID', () => {
    const homes = data.residents.map((r) => r.home_id)
    homes.forEach((h) => expect(h).toMatch(/^home-\d{4}$/))
    expect(new Set(homes).size).toBe(homes.length)
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
    const alertTimes = data.alerts.flatMap((a) =>
      [a.event_ts, a.acknowledged_at, a.resolved_at, a.escalates_at].filter(
        (ts): ts is string => ts !== undefined,
      ),
    )
    expect(alertTimes.length).toBeGreaterThan(data.alerts.length)
    all.concat(alertTimes).forEach((ts) => {
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

  it('every alert has a status and severity, and points at its resident and device', () => {
    expect(data.alerts.length).toBeGreaterThan(0)
    const byId = new Map(data.residents.map((r) => [r.resident_id, r]))
    data.alerts.forEach((a) => {
      expect(['open', 'acknowledged', 'resolved']).toContain(a.status)
      expect(['info', 'warning', 'critical']).toContain(a.severity)
      const resident = byId.get(a.resident_id)
      expect(resident).toBeDefined()
      expect([resident?.wearable.device_id, resident?.bedroom.device_id]).toContain(a.device_id)
    })
    expect(new Set(data.alerts.map((a) => a.alert_id)).size).toBe(data.alerts.length)
  })

  it('has 12 alerts: some open, some acknowledged, none resolved', () => {
    expect(data.alerts).toHaveLength(12)
    const byStatus = (s: string) => data.alerts.filter((a) => a.status === s)
    expect(byStatus('open').length).toBeGreaterThan(0)
    expect(byStatus('acknowledged')).toHaveLength(3)
    expect(byStatus('resolved')).toHaveLength(0)
    data.alerts.forEach((a) => {
      expect(a.resolved_by).toBeUndefined()
      expect(a.resolved_at).toBeUndefined()
    })
  })

  it('acknowledged alerts carry who and when; open ones carry neither', () => {
    data.alerts.forEach((a) => {
      if (a.status === 'acknowledged') {
        expect(a.acknowledged_by).toMatch(/^Test Nurse [A-Z]$/)
        expect(a.acknowledged_at).toBeDefined()
        expect(Date.parse(a.acknowledged_at as string)).toBeGreaterThanOrEqual(Date.parse(a.event_ts))
        expect(Date.parse(a.acknowledged_at as string)).toBeLessThanOrEqual(NOW.getTime())
      } else {
        expect(a.acknowledged_by).toBeUndefined()
        expect(a.acknowledged_at).toBeUndefined()
      }
    })
    const acked = data.alerts.filter((a) => a.status === 'acknowledged')
    expect(acked.map((a) => [a.resident_id, a.alert_type, a.acknowledged_by])).toEqual(
      expect.arrayContaining([
        ['res-0007', 'vitals', 'Test Nurse B'],
        ['res-0015', 'device_offline', 'Test Nurse C'],
        ['res-0013', 'low_battery', 'Test Nurse B'],
      ]),
    )
  })

  it('staff names are display names, never a UUID or Cognito sub', () => {
    const uuid = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i
    data.alerts.forEach((a) => {
      for (const name of [a.acknowledged_by, a.resolved_by]) {
        if (name !== undefined) {
          expect(name).not.toMatch(uuid)
          expect(name).not.toMatch(/^[0-9a-f-]{20,}$/i)
        }
      }
    })
  })

  it('exactly one alert is mid-countdown, about 90 s before it escalates', () => {
    expect(DEMO_CONFIRMATION_WINDOW_MS).toBe(120_000)
    const future = data.alerts.filter((a) => a.escalates_at && Date.parse(a.escalates_at) > NOW.getTime())
    expect(future).toHaveLength(1)
    const [pending] = future
    expect(pending).toMatchObject({
      resident_id: 'res-0008',
      alert_type: 'possible_fall',
      severity: 'warning',
      status: 'open',
    })
    expect(Date.parse(pending.escalates_at as string) - NOW.getTime()).toBe(90_000)
    expect(data.alerts[0]).toBe(pending) // newest
  })

  it('escalates_at is the event time plus the confirmation window; the others have already escalated', () => {
    const windowed = data.alerts.filter((a) => a.escalates_at)
    expect(windowed.map((a) => [a.resident_id, a.alert_type]).sort()).toEqual(
      [
        ['res-0002', 'inactivity'],
        ['res-0008', 'possible_fall'],
        ['res-0012', 'possible_fall'],
      ].sort(),
    )
    windowed.forEach((a) => {
      expect(Date.parse(a.escalates_at as string) - Date.parse(a.event_ts)).toBe(DEMO_CONFIRMATION_WINDOW_MS)
      expect(a.status).toBe('open')
    })
    const passed = windowed.filter((a) => Date.parse(a.escalates_at as string) <= NOW.getTime())
    expect(passed).toHaveLength(2)
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
