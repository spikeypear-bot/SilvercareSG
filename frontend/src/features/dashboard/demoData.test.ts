import type { AlertType, ResidentAlert } from '../../types/dashboard'
import {
  DEMO_ESCALATION_DELAY_MS,
  FAMILY_LINKED_RESIDENT_IDS,
  STATUS_TEXT,
  createDemoData,
  createResidentDetail,
} from './demoData'

const NOW = new Date('2026-10-18T02:30:00Z')
const data = createDemoData(NOW)
/** Written out here rather than imported, so the tests check the demo independently. */
const DEVICE_PROBLEMS: ReadonlySet<AlertType> = new Set<AlertType>(['device_offline', 'battery_low', 'not_worn'])

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
      [a.event_ts, a.acknowledged_at, a.resolved_at, a.escalates_at, a.escalated_at].filter(
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
      expect(['open', 'acknowledged', 'cancelled', 'resolved']).toContain(a.status)
      expect(['warning', 'critical']).toContain(a.severity)
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

  it('acknowledged alerts carry when; health alerts no name, device problems the caregiver and no note', () => {
    data.alerts.forEach((a) => {
      if (DEVICE_PROBLEMS.has(a.alert_type) && a.status === 'acknowledged') {
        expect(a.acknowledged_by_role).toBe('caregiver')
        expect(a.acknowledged_by).toMatch(/^Test Caregiver [AB]$/)
        expect(a.ack_note).toBeUndefined()
      } else {
        expect(a).not.toHaveProperty('acknowledged_by')
        expect(a).not.toHaveProperty('acknowledged_by_role')
      }
      if (a.status === 'acknowledged') {
        expect(a.acknowledged_at).toBeDefined()
        expect(Date.parse(a.acknowledged_at as string)).toBeGreaterThanOrEqual(Date.parse(a.event_ts))
        expect(Date.parse(a.acknowledged_at as string)).toBeLessThanOrEqual(NOW.getTime())
      } else {
        expect(a.acknowledged_at).toBeUndefined()
        expect(a.ack_note).toBeUndefined()
      }
    })
    const acked = data.alerts.filter((a) => a.status === 'acknowledged')
    expect(acked.map((a) => [a.resident_id, a.alert_type])).toEqual(
      expect.arrayContaining([
        ['res-0007', 'hr_high'],
        ['res-0015', 'device_offline'],
        ['res-0013', 'battery_low'],
      ]),
    )
  })

  it('ack notes: res-0007 (nurse, health alert) one line; the device problems res-0015 and res-0013 none', () => {
    const find = (id: string) => data.alerts.find((a) => a.resident_id === id && a.status === 'acknowledged')
    expect(find('res-0007')?.ack_note).toBe('Synthetic note: ambulance on the way, 8 minutes.')
    for (const id of ['res-0015', 'res-0013']) {
      expect(find(id)).toBeDefined()
      expect(find(id)?.ack_note).toBeUndefined()
    }
  })

  it('every resident status follows the backend rule (docs/data-model.md, Resident status)', () => {
    /** The rule, written independently of demoData.ts. */
    const outside = (value: number | null, min?: number, max?: number) =>
      value !== null && ((min !== undefined && value < min) || (max !== undefined && value > max))
    data.residents.forEach((r) => {
      const live = data.alerts.filter(
        (a) => a.resident_id === r.resident_id && (a.status === 'open' || a.status === 'acknowledged'),
      )
      const t = createResidentDetail(r.resident_id, NOW)?.thresholds
      const v = r.vitals[0]
      const abnormal =
        t !== undefined &&
        v !== undefined &&
        (outside(v.hr_bpm, t.hr_bpm.min, t.hr_bpm.max) ||
          outside(v.spo2_pct, t.spo2_pct.min) ||
          outside(v.skin_temp_c, t.skin_temp_c.min, t.skin_temp_c.max))
      const expected = live.some((a) => a.severity === 'critical')
        ? 'urgent'
        : live.length > 0 || abnormal
          ? 'attention'
          : 'ok'
      expect([r.resident_id, r.status]).toEqual([r.resident_id, expected])
    })
  })

  it('a device problem alone makes a resident need attention (res-0013, acknowledged low battery)', () => {
    const m = data.residents.find((r) => r.resident_id === 'res-0013')
    expect(m?.status).toBe('attention')
    expect(m?.status_text).toBe('Needs attention')
  })

  it('staff names are display names, never a UUID or Cognito sub', () => {
    const uuid = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i
    data.alerts.forEach((a) => {
      for (const name of [a.resolved_by, a.cancelled_by]) {
        if (name !== undefined) {
          expect(name).not.toMatch(uuid)
          expect(name).not.toMatch(/^[0-9a-f-]{20,}$/i)
        }
      }
    })
  })

  it('a warning escalates 300 s after its first notification', () => {
    expect(DEMO_ESCALATION_DELAY_MS).toBe(300_000)
  })

  it('exactly one alert is mid-countdown: a warning 2.5 min old, 2.5 min before it escalates', () => {
    const future = data.alerts.filter((a) => a.escalates_at)
    expect(future).toHaveLength(1)
    const [pending] = future
    expect(pending).toMatchObject({
      resident_id: 'res-0008',
      alert_type: 'possible_fall',
      severity: 'warning',
      status: 'open',
    })
    expect(pending.escalated_at).toBeUndefined()
    expect(NOW.getTime() - Date.parse(pending.event_ts)).toBe(150_000)
    expect(Date.parse(pending.escalates_at as string) - NOW.getTime()).toBe(150_000)
    expect(Date.parse(pending.escalates_at as string) - Date.parse(pending.event_ts)).toBe(300_000)
    expect(data.alerts[0]).toBe(pending) // newest
  })

  it('critical alerts never have escalates_at and are sent to the nurse 1 s after event_ts', () => {
    for (const now of [NOW, new Date('2026-11-03T08:00:00Z')]) {
      const critical = createDemoData(now).alerts.filter((a) => a.severity === 'critical')
      expect(critical.length).toBeGreaterThan(0)
      critical.forEach((a) => {
        expect(a.escalates_at).toBeUndefined()
        expect(a.escalated_at).toBeDefined()
        expect(Date.parse(a.escalated_at as string) - Date.parse(a.event_ts)).toBe(1_000)
      })
    }
    // Including the critical one that was acknowledged 3 minutes in.
    const res7 = data.alerts.find((a) => a.resident_id === 'res-0007')
    expect(res7).toMatchObject({ severity: 'critical', status: 'acknowledged' })
    expect(res7?.escalated_at).toBeDefined()
  })

  it('a health warning\'s escalation time is 300 s after event_ts, as escalates_at or escalated_at', () => {
    const warnings = data.alerts.filter((a) => a.severity === 'warning' && !DEVICE_PROBLEMS.has(a.alert_type))
    expect(warnings.length).toBeGreaterThan(0)
    warnings.forEach((a) => {
      const at = a.escalates_at ?? a.escalated_at
      expect(at).toBeDefined()
      expect(Date.parse(at as string) - Date.parse(a.event_ts)).toBe(DEMO_ESCALATION_DELAY_MS)
    })
  })

  it('device problems are never escalated to the nurse (docs/features/watchdog.md)', () => {
    const problems = data.alerts.filter((a) => DEVICE_PROBLEMS.has(a.alert_type))
    expect(problems.map((a) => a.alert_type).sort()).toEqual(
      ['battery_low', 'battery_low', 'device_offline', 'device_offline', 'not_worn'],
    )
    problems.forEach((a) => {
      expect(a.escalates_at).toBeUndefined()
      expect(a.escalated_at).toBeUndefined()
    })
  })

  it('every other health alert has escalated_at in the past: critical at once, warnings after 300 s', () => {
    const others = data.alerts.filter(
      (a) => !DEVICE_PROBLEMS.has(a.alert_type) && !(a.resident_id === 'res-0008' && a.alert_type === 'possible_fall'),
    )
    expect(others).toHaveLength(6)
    others.forEach((a) => {
      expect(a.escalates_at).toBeUndefined()
      expect(a.escalated_at).toBeDefined()
      const delay = a.severity === 'critical' ? 1_000 : DEMO_ESCALATION_DELAY_MS
      expect(Date.parse(a.escalated_at as string) - Date.parse(a.event_ts)).toBe(delay)
      expect(Date.parse(a.escalated_at as string)).toBeLessThanOrEqual(NOW.getTime())
    })
    const ackedEscalated = others.filter((a) => a.status === 'acknowledged').map((a) => a.resident_id)
    expect(ackedEscalated).toEqual(['res-0007'])
  })

  it('follows the demo escalation rule for every alert, at several clock times', () => {
    /** The rule, written independently of demoData.ts. */
    function expected(a: ResidentAlert, now: Date): Pick<ResidentAlert, 'escalates_at' | 'escalated_at'> {
      if (DEVICE_PROBLEMS.has(a.alert_type)) return {}
      if (a.severity === 'critical') return { escalated_at: new Date(Date.parse(a.event_ts) + 1_000).toISOString() }
      const at = Date.parse(a.event_ts) + 300_000
      if (a.acknowledged_at && Date.parse(a.acknowledged_at) <= at) return {}
      const iso = new Date(at).toISOString()
      return at <= now.getTime() ? { escalated_at: iso } : { escalates_at: iso }
    }
    for (const now of [NOW, new Date('2026-11-03T08:00:00Z'), new Date('2026-12-31T23:59:59.999Z')]) {
      createDemoData(now).alerts.forEach((a) => {
        expect({ escalates_at: a.escalates_at, escalated_at: a.escalated_at }).toEqual({
          escalates_at: undefined,
          escalated_at: undefined,
          ...expected(a, now),
        })
        // Never both fields at once.
        expect(a.escalates_at !== undefined && a.escalated_at !== undefined).toBe(false)
      })
    }
  })

  it('uses the new alert types: res-0007 hr_high (critical), res-0019 spo2_low (warning)', () => {
    const find = (id: string) => data.alerts.filter((a) => a.resident_id === id)
    expect(find('res-0007')).toEqual([
      expect.objectContaining({ alert_type: 'hr_high', severity: 'critical', status: 'acknowledged' }),
    ])
    expect(find('res-0019')).toEqual([
      expect.objectContaining({ alert_type: 'spo2_low', severity: 'warning', status: 'open' }),
    ])
    expect(data.alerts.map((a) => a.alert_type)).not.toContain('vitals')
  })

  it('res-0013 battery_low and res-0002 possible_fall (410 min old) are warnings', () => {
    const batteryLow = data.alerts.find((a) => a.resident_id === 'res-0013' && a.alert_type === 'battery_low')
    expect(batteryLow?.severity).toBe('warning')
    const oldFall = data.alerts.find((a) => a.resident_id === 'res-0002' && a.alert_type === 'possible_fall')
    expect(oldFall?.severity).toBe('warning')
    expect(NOW.getTime() - Date.parse(oldFall?.event_ts as string)).toBe(410 * 60_000)
  })

  it('alerts are newest first', () => {
    const times = data.alerts.map((a) => a.event_ts)
    expect([...times].sort().reverse()).toEqual(times)
  })

  it('includes every status and severity so each badge can be seen', () => {
    expect(new Set(data.residents.map((r) => r.status))).toEqual(new Set(['ok', 'attention', 'urgent']))
    expect(new Set(data.alerts.map((a) => a.severity))).toEqual(new Set(['warning', 'critical']))
  })
})
