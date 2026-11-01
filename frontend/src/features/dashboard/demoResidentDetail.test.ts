import type { ResidentDetail, ThresholdKey, VitalsThresholds } from '../../types/resident'
import { DEMO_DEFAULT_THRESHOLDS, createDemoData, createResidentDetail, residentId } from './demoData'

const NOW = new Date('2026-10-18T02:30:00Z')
const DAY_MS = 24 * 60 * 60_000
const IDS = Array.from({ length: 20 }, (_, i) => residentId(i + 1))
const details = IDS.map((id) => createResidentDetail(id, NOW) as ResidentDetail)
const detailOf = (id: string) => details[IDS.indexOf(id)]

/** Reads one threshold by its history key, e.g. 'hr_bpm.max'. */
function getValue(t: VitalsThresholds, key: ThresholdKey): number {
  const [metric, bound] = key.split('.') as [keyof VitalsThresholds, 'min' | 'max']
  return (t[metric] as Record<string, number>)[bound]
}

/** A copy of `t` with one threshold set (the test's own version, not the app's). */
function setValue(t: VitalsThresholds, key: ThresholdKey, value: number): VitalsThresholds {
  const [metric, bound] = key.split('.') as [keyof VitalsThresholds, 'min' | 'max']
  return { ...t, [metric]: { ...t[metric], [bound]: value } }
}

const newestFirst = (times: string[]) => expect([...times].sort().reverse()).toEqual(times)

describe('createResidentDetail', () => {
  it.each(['res-9999', 'res-0000', 'res-0021', '', 'Test Resident A', 'RES-0001'])(
    'returns null for unknown id "%s"',
    (id) => {
      expect(createResidentDetail(id, NOW)).toBeNull()
    },
  )

  it('returns a detail for every demo resident, matching the dashboard resident', () => {
    const { residents } = createDemoData(NOW)
    details.forEach((d, i) => {
      expect(d).not.toBeNull()
      expect(d.resident).toEqual(residents[i])
      expect(d.wearable?.device_id).toBe(residents[i].wearable.device_id)
      expect(d.bedroom?.device_id).toBe(residents[i].bedroom.device_id)
      expect(d.wearable?.online).toBe(residents[i].wearable.online)
      expect(d.bedroom?.online).toBe(residents[i].bedroom.online)
    })
  })

  it('every latest list has at most 5 entries, newest first, with unique message IDs', () => {
    details.forEach((d) => {
      const lists = [
        d.wearable?.latest.vitals,
        d.wearable?.latest.fall,
        d.wearable?.latest.status,
        d.bedroom?.latest.motion,
        d.bedroom?.latest.bed,
        d.bedroom?.latest.status,
      ]
      lists.forEach((list) => {
        expect(list).toBeDefined()
        expect(list!.length).toBeLessThanOrEqual(5)
        newestFirst(list!.map((e) => e.ts))
        list!.forEach((e) => expect(new Date(e.ts).getTime()).toBeLessThanOrEqual(NOW.getTime()))
      })
      const messageIds = lists.flatMap((l) => l!.map((e) => e.message_id))
      expect(new Set(messageIds).size).toBe(messageIds.length)
    })
  })

  it('message entries keep only the metrics.md fields', () => {
    const d = detailOf('res-0003')
    const keys = (o: object) => Object.keys(o).sort()
    expect(keys(d.wearable!.latest.vitals[0])).toEqual(
      ['activity_pct', 'hr_bpm', 'message_id', 'skin_temp_c', 'spo2_pct', 'ts'].sort(),
    )
    expect(keys(d.wearable!.latest.fall[0])).toEqual(['confidence', 'message_id', 'peak_g', 'still_s', 'ts'])
    expect(keys(d.wearable!.latest.status[0])).toEqual(
      ['battery_pct', 'charging', 'fw_version', 'message_id', 'ts', 'worn'].sort(),
    )
    expect(keys(d.bedroom!.latest.motion[0])).toEqual(['message_id', 'motion', 'ts'])
    expect(keys(d.bedroom!.latest.bed[0])).toEqual(['message_id', 'occupied', 'ts'])
    expect(keys(d.bedroom!.latest.status[0])).toEqual(['battery_pct', 'fw_version', 'message_id', 'ts'])
    details.forEach((x) =>
      x.wearable!.latest.fall.forEach((f) => {
        expect(f.confidence).toBeGreaterThanOrEqual(0)
        expect(f.confidence).toBeLessThanOrEqual(1)
      }),
    )
  })

  it('alerts are newest first, have unique alert_ids, and belong to the resident', () => {
    const allIds: string[] = []
    details.forEach((d) => {
      newestFirst(d.alerts.map((a) => a.event_ts))
      d.alerts.forEach((a) => expect(a.resident_id).toBe(d.resident.resident_id))
      const deviceIds = [d.wearable?.device_id, d.bedroom?.device_id]
      d.alerts.forEach((a) => expect(deviceIds).toContain(a.device_id))
      allIds.push(...d.alerts.map((a) => a.alert_id))
    })
    expect(new Set(allIds).size).toBe(allIds.length)
  })

  it('history alerts are resolved, with resolved_by/at, within the last 30 days', () => {
    const { alerts: openAlerts } = createDemoData(NOW)
    const openIds = new Set(openAlerts.map((a) => a.alert_id))
    const history = details.flatMap((d) => d.alerts.filter((a) => !openIds.has(a.alert_id)))
    expect(history.length).toBeGreaterThan(0)
    history.forEach((a) => {
      expect(a.status).toBe('resolved')
      expect(a.resolved_by).toMatch(/^Test /)
      expect(a.resolved_at).toBeDefined()
      const event = Date.parse(a.event_ts)
      const resolvedAt = Date.parse(a.resolved_at!)
      expect(event).toBeGreaterThan(NOW.getTime() - 30 * DAY_MS)
      expect(resolvedAt).toBeGreaterThanOrEqual(event)
      expect(resolvedAt).toBeLessThanOrEqual(NOW.getTime())
      if (a.acknowledged_at) {
        expect(a.acknowledged_by).toMatch(/^Test /)
        expect(Date.parse(a.acknowledged_at)).toBeGreaterThanOrEqual(event)
        expect(Date.parse(a.acknowledged_at)).toBeLessThanOrEqual(resolvedAt)
      }
    })
    // Some were acknowledged before being resolved, some were not.
    expect(history.some((a) => a.acknowledged_by)).toBe(true)
    expect(history.some((a) => !a.acknowledged_by)).toBe(true)
  })

  it("the detail's unresolved alerts match createDemoData for that resident", () => {
    const { alerts } = createDemoData(NOW)
    details.forEach((d) => {
      const unresolved = d.alerts.filter((a) => a.status !== 'resolved')
      expect(unresolved).toEqual(alerts.filter((a) => a.resident_id === d.resident.resident_id))
    })
  })

  it('the dashboard data still has no resolved alerts', () => {
    const { alerts } = createDemoData(NOW)
    expect(alerts.every((a) => a.status !== 'resolved')).toBe(true)
    expect(alerts.every((a) => a.resolved_by === undefined && a.resolved_at === undefined)).toBe(true)
  })

  it('every name is synthetic ("Test ...")', () => {
    details.forEach((d) => {
      expect(d.resident.name).toMatch(/^Test Resident [A-T]$/)
      expect(d.thresholds.updated_by).toMatch(/^Test /)
      d.alerts.forEach((a) => {
        if (a.acknowledged_by) expect(a.acknowledged_by).toMatch(/^Test /)
        if (a.resolved_by) expect(a.resolved_by).toMatch(/^Test /)
      })
    })
  })

  it('res-0014 has no alerts at all', () => {
    expect(detailOf('res-0014').alerts).toEqual([])
  })

  it('res-0001 has no falls; res-0003 and res-0017 have fall rows matching their fall alerts', () => {
    expect(detailOf('res-0001').wearable!.latest.fall).toEqual([])
    for (const id of ['res-0003', 'res-0017']) {
      const d = detailOf(id)
      const fallAlertTimes = d.alerts.filter((a) => a.alert_type === 'fall').map((a) => a.event_ts)
      expect(d.wearable!.latest.fall.length).toBeGreaterThan(0)
      expect(d.wearable!.latest.fall.map((f) => f.ts)).toEqual(fallAlertTimes.slice(0, 5))
    }
  })

  it('res-0001 has a mains-powered bedroom sensor (battery_pct null)', () => {
    detailOf('res-0001').bedroom!.latest.status.forEach((s) => expect(s.battery_pct).toBeNull())
    detailOf('res-0002').bedroom!.latest.status.forEach((s) => expect(s.battery_pct).not.toBeNull())
  })

  it("the newest bed entry for res-0008 is out of bed at its possible_fall alert's event_ts", () => {
    const d = detailOf('res-0008')
    const possibleFall = d.alerts.find((a) => a.alert_type === 'possible_fall' && a.status !== 'resolved')
    expect(possibleFall).toBeDefined()
    expect(d.bedroom!.latest.bed[0]).toMatchObject({ ts: possibleFall!.event_ts, occupied: false })
  })

  it('res-0011 has HR max 120 and res-0019 SpO2 min 88; every other resident has the defaults', () => {
    details.forEach((d) => {
      const { updated_at: _at, updated_by: _by, ...values } = d.thresholds
      const id = d.resident.resident_id
      if (id === 'res-0011') {
        expect(values).toEqual({ ...DEMO_DEFAULT_THRESHOLDS, hr_bpm: { min: 50, max: 120 } })
      } else if (id === 'res-0019') {
        expect(values).toEqual({ ...DEMO_DEFAULT_THRESHOLDS, spo2_pct: { min: 88 } })
      } else {
        expect(values).toEqual(DEMO_DEFAULT_THRESHOLDS)
      }
    })
  })

  it('applying each history oldest first to the defaults gives the thresholds', () => {
    details.forEach((d) => {
      const applied = [...d.threshold_history]
        .reverse()
        .flatMap((e) => e.changes)
        .reduce((t, c) => setValue(t, c.field, c.new_value), DEMO_DEFAULT_THRESHOLDS)
      const { updated_at: _at, updated_by: _by, ...values } = d.thresholds
      expect(values, d.resident.resident_id).toEqual(applied)
    })
  })

  it("each change's old_value is the value just before it (starting from the defaults)", () => {
    const mismatches: string[] = []
    details.forEach((d) => {
      let current = DEMO_DEFAULT_THRESHOLDS
      ;[...d.threshold_history].reverse().forEach((entry) => {
        entry.changes.forEach((c) => {
          const before = getValue(current, c.field)
          if (c.old_value !== before) {
            mismatches.push(`${d.resident.resident_id} ${c.field}: old_value ${c.old_value}, was ${before}`)
          }
          current = setValue(current, c.field, c.new_value)
        })
      })
    })
    expect(mismatches).toEqual([])
  })

  it("updated_at/updated_by are the newest entry's changed_at/changed_by, or Test Doctor A at 20+n days", () => {
    details.forEach((d, i) => {
      const n = i + 1
      const newest = d.threshold_history[0]
      if (newest) {
        expect(d.thresholds.updated_at).toBe(newest.changed_at)
        expect(d.thresholds.updated_by).toBe(newest.changed_by)
      } else {
        expect(d.thresholds.updated_by).toBe('Test Doctor A')
        expect(d.thresholds.updated_at).toBe(new Date(NOW.getTime() - (20 + n) * DAY_MS).toISOString())
      }
    })
  })

  it('history entries are newest first, in the past, with unique change_ids, by Test Doctor A or B', () => {
    const allIds: string[] = []
    details.forEach((d) => {
      newestFirst(d.threshold_history.map((e) => e.changed_at))
      d.threshold_history.forEach((e) => {
        expect(Date.parse(e.changed_at)).toBeLessThan(NOW.getTime())
        expect(['Test Doctor A', 'Test Doctor B']).toContain(e.changed_by)
        expect(e.changes.length).toBeGreaterThan(0)
        e.changes.forEach((c) => expect(c.old_value).not.toBe(c.new_value))
        expect(new Set(e.changes.map((c) => c.field)).size).toBe(e.changes.length)
        allIds.push(e.change_id)
      })
    })
    expect(new Set(allIds).size).toBe(allIds.length)
  })

  it('follows the seeds for each resident number n', () => {
    const daysAgo = (iso: string) => (NOW.getTime() - Date.parse(iso)) / DAY_MS
    details.forEach((d, i) => {
      const n = i + 1
      const h = d.threshold_history
      const id = d.resident.resident_id
      if (n === 11) {
        expect(h).toHaveLength(1)
        expect(h[0]).toMatchObject({ changed_by: 'Test Doctor A' })
        expect(h[0].changes).toEqual([{ field: 'hr_bpm.max', old_value: 110, new_value: 120 }])
        expect(daysAgo(h[0].changed_at)).toBe(12)
      } else if (n === 19) {
        expect(h).toHaveLength(1)
        expect(h[0]).toMatchObject({ changed_by: 'Test Doctor B' })
        expect(h[0].changes).toEqual([{ field: 'spo2_pct.min', old_value: 92, new_value: 88 }])
        expect(daysAgo(h[0].changed_at)).toBe(9)
      } else if (n % 3 === 1) {
        expect(h, id).toHaveLength(2)
        const by = n % 2 === 0 ? 'Test Doctor A' : 'Test Doctor B'
        expect(h.map((e) => e.changed_by)).toEqual([by, by])
        expect(h.map((e) => daysAgo(e.changed_at))).toEqual([4 + n, 10 + n])
        expect(h[0].changes).toEqual([{ field: 'skin_temp_c.min', old_value: 30.5, new_value: 31.0 }])
        expect(h[1].changes).toEqual([{ field: 'skin_temp_c.min', old_value: 31.0, new_value: 30.5 }])
      } else if (n % 3 === 2) {
        expect(h, id).toHaveLength(2)
        const by = n % 2 === 0 ? 'Test Doctor A' : 'Test Doctor B'
        expect(h.map((e) => e.changed_by)).toEqual([by, by])
        expect(h.map((e) => daysAgo(e.changed_at))).toEqual([3 + n, 6 + n])
        expect(h[0].changes).toEqual([
          { field: 'hr_bpm.min', old_value: 45, new_value: 50 },
          { field: 'hr_bpm.max', old_value: 115, new_value: 110 },
        ])
        expect(h[1].changes).toEqual([
          { field: 'hr_bpm.min', old_value: 50, new_value: 45 },
          { field: 'hr_bpm.max', old_value: 110, new_value: 115 },
        ])
      } else {
        expect(h, id).toEqual([])
      }
    })
    expect(['res-0003', 'res-0006', 'res-0009', 'res-0012', 'res-0015', 'res-0018'].map((id) => detailOf(id).threshold_history)).toEqual(
      Array(6).fill([]),
    )
  })

  it('is deterministic for the same now', () => {
    expect(createResidentDetail('res-0005', NOW)).toEqual(createResidentDetail('res-0005', NOW))
  })
})
