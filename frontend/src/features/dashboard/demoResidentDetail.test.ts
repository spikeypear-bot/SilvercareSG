import type { ResidentAlert } from '../../types/dashboard'
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

/** What the API returns once the actor's account is deleted (docs/data-model.md, Actor names). */
const FORMER_STAFF = /^Former staff member$/
/** A synthetic "Test ..." name or the exact "Former staff member" placeholder. */
const ACTOR_NAME = /^(Test .+|Former staff member)$/
/** A synthetic caregiver name or the "Former caregiver" placeholder. */
const CAREGIVER_NAME = /^(Test Caregiver [A-Z]|Former caregiver)$/
/** History alert types that come from the wearable. */
const WEARABLE_TYPES = new Set<ResidentAlert['alert_type']>(['battery_low', 'hr_high', 'fall', 'not_worn'])
/** Device problems: acknowledged by a caregiver (no note), never cancelled. */
const DEVICE_PROBLEMS = new Set<ResidentAlert['alert_type']>(['device_offline', 'battery_low', 'not_worn'])

/** History alerts: those not in the dashboard data (which holds the open-list ones). */
function historyOf(list: ResidentDetail[]): ResidentAlert[] {
  const openIds = new Set(createDemoData(NOW).alerts.map((a) => a.alert_id))
  return list.flatMap((d) => d.alerts.filter((a) => !openIds.has(a.alert_id)))
}

/** Resident index i and entry index k, read back from a demo history alert_id. */
function historyIndex(alertId: string): { i: number; k: number } {
  const m = /^6f1c2e0a-5b3d-5e8f-9a47-1(\d{3})(\d{8})$/.exec(alertId)
  if (!m) throw new Error(`Not a demo history alert ID: ${alertId}`)
  return { i: Number(m[1]) - 1, k: Number(m[2]) - 1 }
}

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

  it('history alerts are resolved or cancelled, within the last 30 days', () => {
    const history = historyOf(details)
    expect(history.length).toBeGreaterThan(0)
    history.forEach((a) => {
      expect(['resolved', 'cancelled']).toContain(a.status)
      expect(Date.parse(a.event_ts)).toBeGreaterThan(NOW.getTime() - 30 * DAY_MS)
      expect(Date.parse(a.event_ts)).toBeLessThanOrEqual(NOW.getTime())
    })
    // Both kinds are in the demo.
    expect(history.some((a) => a.status === 'resolved')).toBe(true)
    expect(history.some((a) => a.status === 'cancelled')).toBe(true)
  })

  it('health alerts have no acknowledged_by key (the nurse has no name); acknowledged device problems name the caregiver', () => {
    const all = details.flatMap((d) => d.alerts)
    expect(all.length).toBeGreaterThan(0)
    all.forEach((a) => {
      if (DEVICE_PROBLEMS.has(a.alert_type) && a.acknowledged_at) {
        expect(a.acknowledged_by_role, a.alert_id).toBe('caregiver')
        expect(a.acknowledged_by, a.alert_id).toMatch(CAREGIVER_NAME)
      } else {
        expect(a, a.alert_id).not.toHaveProperty('acknowledged_by')
        expect(a, a.alert_id).not.toHaveProperty('acknowledged_by_role')
      }
    })
    expect(all.some((a) => a.acknowledged_at && !DEVICE_PROBLEMS.has(a.alert_type))).toBe(true)
    expect(all.some((a) => a.acknowledged_at && DEVICE_PROBLEMS.has(a.alert_type))).toBe(true)
  })

  it('resolved history alerts have resolved_at in order (a staff name only on health alerts), some acknowledged first', () => {
    const resolved = historyOf(details).filter((a) => a.status === 'resolved')
    expect(resolved.length).toBeGreaterThan(0)
    resolved.forEach((a) => {
      // Device problems are resolved by the watchdog (`system`): no name.
      if (DEVICE_PROBLEMS.has(a.alert_type)) expect(a, a.alert_id).not.toHaveProperty('resolved_by')
      else expect(a.resolved_by).toMatch(ACTOR_NAME)
      expect(a.resolved_at).toBeDefined()
      const event = Date.parse(a.event_ts)
      const resolvedAt = Date.parse(a.resolved_at!)
      expect(resolvedAt).toBeGreaterThanOrEqual(event)
      expect(resolvedAt).toBeLessThanOrEqual(NOW.getTime())
      if (a.acknowledged_at) {
        expect(Date.parse(a.acknowledged_at)).toBeGreaterThanOrEqual(event)
        expect(Date.parse(a.acknowledged_at)).toBeLessThanOrEqual(resolvedAt)
      } else {
        expect(a, a.alert_id).not.toHaveProperty('ack_note')
      }
      expect(a).not.toHaveProperty('cancelled_at')
      expect(a).not.toHaveProperty('cancelled_by_role')
      expect(a).not.toHaveProperty('cancelled_by')
    })
    // Some were acknowledged before being resolved, some were not.
    expect(resolved.some((a) => a.acknowledged_at)).toBe(true)
    expect(resolved.some((a) => !a.acknowledged_at)).toBe(true)
  })

  it('ack notes: only on acknowledged history health alerts, exactly when (i + k) is even, all synthetic', () => {
    const mismatches: string[] = []
    const history = historyOf(details)
    history.forEach((a) => {
      const { i, k } = historyIndex(a.alert_id)
      const expectNote = a.acknowledged_at !== undefined && !DEVICE_PROBLEMS.has(a.alert_type) && (i + k) % 2 === 0
      if ((a.ack_note !== undefined) !== expectNote) mismatches.push(`${a.alert_id} ack_note ${a.ack_note}`)
      if (a.ack_note !== undefined) expect(a.ack_note).toBe('Synthetic note: the care centre is calling the resident.')
    })
    expect(mismatches).toEqual([])
    expect(history.some((a) => a.ack_note)).toBe(true)
    // Some acknowledged history alerts have no note.
    expect(history.some((a) => a.acknowledged_at && !a.ack_note)).toBe(true)
  })

  it('resident cancels: cancelled_at is event_ts + 30 s, with no ack, resolve, or caregiver fields', () => {
    const cancelled = historyOf(details).filter((a) => a.status === 'cancelled' && a.cancelled_by_role === 'resident')
    expect(cancelled.length).toBeGreaterThan(0)
    cancelled.forEach((a) => {
      expect(Date.parse(a.cancelled_at!) - Date.parse(a.event_ts)).toBe(30_000)
      expect(Date.parse(a.cancelled_at!)).toBeLessThanOrEqual(NOW.getTime())
      for (const field of ['acknowledged_at', 'ack_note', 'resolved_by', 'resolved_at', 'cancelled_by'] as const) {
        expect(a, `${a.alert_id} ${field}`).not.toHaveProperty(field)
      }
    })
  })

  it('caregiver cancels: a synthetic caregiver name, cancelled_at is event_ts + 10 min, no resolve fields', () => {
    const cancelled = historyOf(details).filter((a) => a.status === 'cancelled' && a.cancelled_by_role === 'caregiver')
    expect(cancelled.length).toBeGreaterThan(0)
    cancelled.forEach((a) => {
      expect(a.cancelled_by).toMatch(CAREGIVER_NAME)
      expect(Date.parse(a.cancelled_at!) - Date.parse(a.event_ts)).toBe(10 * 60_000)
      expect(Date.parse(a.cancelled_at!)).toBeLessThanOrEqual(NOW.getTime())
      if (a.acknowledged_at) {
        // Cancelled after the acknowledge.
        expect(Date.parse(a.acknowledged_at)).toBeGreaterThanOrEqual(Date.parse(a.event_ts))
        expect(Date.parse(a.acknowledged_at)).toBeLessThan(Date.parse(a.cancelled_at!))
      }
      for (const field of ['resolved_by', 'resolved_at'] as const) {
        expect(a, `${a.alert_id} ${field}`).not.toHaveProperty(field)
      }
    })
    // Both kinds: cancelled after an acknowledge, and cancelled while still open.
    expect(cancelled.some((a) => a.acknowledged_at)).toBe(true)
    expect(cancelled.some((a) => !a.acknowledged_at)).toBe(true)
  })

  it('every cancel has a role: resident or caregiver', () => {
    const cancelled = historyOf(details).filter((a) => a.status === 'cancelled')
    expect(new Set(cancelled.map((a) => a.cancelled_by_role))).toEqual(new Set(['resident', 'caregiver']))
  })

  it('history status follows the rule: device problems resolved; else wearable and (i + k) % 4 === 1 → resident cancel; else (i + k) % 5 === 3 → caregiver cancel; else resolved', () => {
    const mismatches: string[] = []
    details.forEach((d) => {
      historyOf([d]).forEach((a) => {
        const { i, k } = historyIndex(a.alert_id)
        expect(i).toBe(IDS.indexOf(d.resident.resident_id))
        const fromWearable = a.device_id === d.wearable!.device_id
        expect(fromWearable, a.alert_id).toBe(WEARABLE_TYPES.has(a.alert_type))
        // No cancel exists for device problems ("I'm OK" and the caregiver Cancel are for health alerts).
        const expected = DEVICE_PROBLEMS.has(a.alert_type)
          ? 'resolved'
          : fromWearable && (i + k) % 4 === 1
            ? 'cancelled resident'
            : (i + k) % 5 === 3
              ? 'cancelled caregiver'
              : 'resolved'
        const actual = a.status === 'cancelled' ? `cancelled ${a.cancelled_by_role}` : a.status
        if (actual !== expected) mismatches.push(`${a.alert_id}: ${actual}, expected ${expected}`)
        // Acknowledged first exactly when (i + k) % 3 !== 0, never on a resident cancel.
        const expectAck = expected !== 'cancelled resident' && (i + k) % 3 !== 0
        if ((a.acknowledged_at !== undefined) !== expectAck) mismatches.push(`${a.alert_id}: acknowledged_at`)
      })
    })
    expect(mismatches).toEqual([])
  })

  it('caregiver names follow the rule: Test Caregiver A/B by (i + k) % 2, "Former caregiver" when (i + k) % 7 === 5', () => {
    const mismatches: string[] = []
    historyOf(details)
      .filter((a) => a.cancelled_by_role === 'caregiver')
      .forEach((a) => {
        const { i, k } = historyIndex(a.alert_id)
        const expected =
          (i + k) % 7 === 5 ? 'Former caregiver' : ['Test Caregiver A', 'Test Caregiver B'][(i + k) % 2]
        if (a.cancelled_by !== expected) mismatches.push(`${a.alert_id}: ${a.cancelled_by}, expected ${expected}`)
      })
    expect(mismatches).toEqual([])
  })

  it('resident cancels are wearable alerts only: res-0002, res-0004 and res-0016, each a high heart rate', () => {
    const cancelled = details.flatMap((d) =>
      historyOf([d])
        .filter((a) => a.status === 'cancelled' && a.cancelled_by_role === 'resident')
        .map((a) => {
          expect(a.device_id).toBe(d.wearable!.device_id)
          return [d.resident.resident_id, a.alert_type]
        }),
    )
    expect(cancelled).toEqual([
      ['res-0002', 'hr_high'],
      ['res-0004', 'hr_high'],
      ['res-0016', 'hr_high'],
    ])
    // A bedroom alert where (i + k) % 4 === 1 is not a resident cancel: res-0010's first (i = 9, k = 0).
    const bedroom = historyOf([detailOf('res-0010')]).find((a) => historyIndex(a.alert_id).k === 0)!
    expect(bedroom.device_id).toBe(detailOf('res-0010').bedroom!.device_id)
    expect(bedroom.status).toBe('resolved')
  })

  it('"Former staff member" is the resolver exactly when (i + k) % 7 === 5, on resolved alerts only', () => {
    const mismatches: string[] = []
    const formerAt: string[] = []
    details.forEach((d) => {
      historyOf([d]).forEach((a) => {
        if (a.status !== 'resolved' || DEVICE_PROBLEMS.has(a.alert_type)) {
          expect(a.resolved_by).toBeUndefined()
          return
        }
        const { i, k } = historyIndex(a.alert_id)
        const former = (i + k) % 7 === 5
        const expected = former ? FORMER_STAFF : /^Test Nurse [ABC]$/
        if (!expected.test(String(a.resolved_by))) mismatches.push(`${a.alert_id} resolved_by ${a.resolved_by}`)
        if (former) formerAt.push(`${d.resident.resident_id} k=${k}${a.acknowledged_at ? ' ack' : ''}`)
      })
    })
    expect(mismatches).toEqual([])
    // res-0006 k=0 (not_worn) and res-0013 k=0 (battery_low) are device problems now
    // resolved by the watchdog, so they have no resolver name.
    expect(formerAt).toEqual(['res-0011 k=2', 'res-0012 k=1', 'res-0019 k=1 ack', 'res-0020 k=0 ack'])
  })

  it("the detail's open-list alerts (open and acknowledged) match createDemoData for that resident", () => {
    const { alerts } = createDemoData(NOW)
    details.forEach((d) => {
      const openList = d.alerts.filter((a) => a.status === 'open' || a.status === 'acknowledged')
      expect(openList).toEqual(alerts.filter((a) => a.resident_id === d.resident.resident_id))
    })
  })

  it('the dashboard data has only open and acknowledged alerts: no resolved or cancelled ones', () => {
    const { alerts } = createDemoData(NOW)
    expect(alerts.length).toBeGreaterThan(0)
    expect(alerts.every((a) => a.status === 'open' || a.status === 'acknowledged')).toBe(true)
    expect(alerts.every((a) => a.resolved_by === undefined && a.resolved_at === undefined)).toBe(true)
    expect(alerts.every((a) => a.cancelled_at === undefined && a.cancelled_by_role === undefined)).toBe(true)
  })

  it('every name is synthetic ("Test ...", or the "Former staff member" placeholder)', () => {
    details.forEach((d) => {
      expect(d.resident.name).toMatch(/^Test Resident [A-T]$/)
      if (d.thresholds.updated_by !== undefined) expect(d.thresholds.updated_by).toMatch(/^Test /)
      d.threshold_history.forEach((e) => expect(e.changed_by).toMatch(/^Test /))
      d.alerts.forEach((a) => {
        if (a.resolved_by) expect(a.resolved_by).toMatch(ACTOR_NAME)
        if (a.cancelled_by) expect(a.cancelled_by).toMatch(CAREGIVER_NAME)
      })
    })
    // The placeholder is exactly this string; anything else must still be "Test ...".
    expect('Former staff member').toMatch(ACTOR_NAME)
    for (const bad of ['Former staff', 'Former staff member X', 'former staff member', 'Former caregiver', 'Jane Tan']) {
      expect(bad).not.toMatch(ACTOR_NAME)
    }
    expect('Former caregiver').toMatch(CAREGIVER_NAME)
    for (const bad of ['Former staff member', 'former caregiver', 'Test Nurse A', 'Jane Tan']) {
      expect(bad).not.toMatch(CAREGIVER_NAME)
    }
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

  it("the defaults are main's decided values (2026-11-02)", () => {
    expect(DEMO_DEFAULT_THRESHOLDS).toEqual({
      hr_bpm: { min: 50, max: 120 },
      spo2_pct: { min: 92 },
      skin_temp_c: { min: 30.0, max: 38.0 },
    })
  })

  it('only res-0002 has its own limit (SpO2 min 88); every other resident has the defaults', () => {
    details.forEach((d) => {
      const { updated_at: _at, updated_by: _by, ...values } = d.thresholds
      if (d.resident.resident_id === 'res-0002') {
        expect(values).toEqual({ ...DEMO_DEFAULT_THRESHOLDS, spo2_pct: { min: 88 } })
      } else {
        expect(values, d.resident.resident_id).toEqual(DEMO_DEFAULT_THRESHOLDS)
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

  it("updated_at/updated_by are the newest entry's changed_at/changed_by, or absent without history", () => {
    details.forEach((d) => {
      const newest = d.threshold_history[0]
      if (newest) {
        expect(d.thresholds.updated_at).toBe(newest.changed_at)
        expect(d.thresholds.updated_by).toBe(newest.changed_by)
      } else {
        expect(d.thresholds, d.resident.resident_id).not.toHaveProperty('updated_at')
        expect(d.thresholds, d.resident.resident_id).not.toHaveProperty('updated_by')
      }
    })
    // n % 3 == 0 residents have no history, so no by-line fields.
    expect(detailOf('res-0003').thresholds).not.toHaveProperty('updated_by')
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
      if (n === 2) {
        expect(h).toHaveLength(1)
        expect(h[0]).toMatchObject({ changed_by: 'Test Doctor B' })
        expect(h[0].changes).toEqual([{ field: 'spo2_pct.min', old_value: 92, new_value: 88 }])
        expect(daysAgo(h[0].changed_at)).toBe(9)
      } else if (n % 3 === 1) {
        expect(h, id).toHaveLength(2)
        const by = n % 2 === 0 ? 'Test Doctor A' : 'Test Doctor B'
        expect(h.map((e) => e.changed_by)).toEqual([by, by])
        expect(h.map((e) => daysAgo(e.changed_at))).toEqual([4 + n, 10 + n])
        expect(h[0].changes).toEqual([{ field: 'skin_temp_c.min', old_value: 29.5, new_value: 30.0 }])
        expect(h[1].changes).toEqual([{ field: 'skin_temp_c.min', old_value: 30.0, new_value: 29.5 }])
      } else if (n % 3 === 2) {
        expect(h, id).toHaveLength(2)
        const by = n % 2 === 0 ? 'Test Doctor A' : 'Test Doctor B'
        expect(h.map((e) => e.changed_by)).toEqual([by, by])
        expect(h.map((e) => daysAgo(e.changed_at))).toEqual([3 + n, 6 + n])
        expect(h[0].changes).toEqual([
          { field: 'hr_bpm.min', old_value: 45, new_value: 50 },
          { field: 'hr_bpm.max', old_value: 125, new_value: 120 },
        ])
        expect(h[1].changes).toEqual([
          { field: 'hr_bpm.min', old_value: 50, new_value: 45 },
          { field: 'hr_bpm.max', old_value: 120, new_value: 125 },
        ])
      } else {
        expect(h, id).toEqual([])
      }
    })
    // res-0011 and res-0019 no longer have their own variants: they follow the generic seeds.
    expect(detailOf('res-0011').threshold_history).toHaveLength(2)
    expect(detailOf('res-0019').threshold_history).toHaveLength(2)
    expect(detailOf('res-0019').threshold_history[0].changes[0].field).toBe('skin_temp_c.min')
    expect(['res-0003', 'res-0006', 'res-0009', 'res-0012', 'res-0015', 'res-0018'].map((id) => detailOf(id).threshold_history)).toEqual(
      Array(6).fill([]),
    )
  })

  it('is deterministic for the same now', () => {
    expect(createResidentDetail('res-0005', NOW)).toEqual(createResidentDetail('res-0005', NOW))
  })
})
