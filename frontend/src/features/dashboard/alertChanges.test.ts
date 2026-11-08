import { describe, expect, it } from 'vitest'
import type { ResidentAlert } from '../../types/dashboard'
import { diffAlerts, knownAlerts } from './alertChanges'
import { applyOverrides, OVERRIDE_TTL_MS, pruneOverrides } from './useDashboardData'

// Synthetic alerts only.
function alert(id: string, status: ResidentAlert['status'], extra: Partial<ResidentAlert> = {}): ResidentAlert {
  return {
    alert_id: id,
    resident_id: 'res-0001',
    device_id: 'wearable-0001',
    alert_type: 'fall',
    severity: 'critical',
    status,
    event_ts: '2026-11-08T01:00:00.000Z',
    ...extra,
  }
}

describe('diffAlerts', () => {
  it('reports a new open alert once, even if a poll leaves it out and it comes back', () => {
    let known = knownAlerts([alert('a1', 'open')])
    let r = diffAlerts(known, [alert('a1', 'open'), alert('a2', 'open')], false)
    expect(r.changes.map((c) => [c.kind, c.alert.alert_id])).toEqual([['new', 'a2']])
    known = r.known
    r = diffAlerts(known, [alert('a1', 'open')], false)
    expect(r.changes).toEqual([])
    r = diffAlerts(r.known, [alert('a1', 'open'), alert('a2', 'open')], false)
    expect(r.changes).toEqual([])
  })

  it('reports nothing when a poll returns the same statuses', () => {
    const list = [alert('a1', 'open'), alert('a2', 'acknowledged')]
    const r = diffAlerts(knownAlerts(list), list.map((a) => ({ ...a })), true)
    expect(r.changes).toEqual([])
  })

  it('reports acknowledge, resolve, and cancel from the open list', () => {
    const known = knownAlerts([alert('a1', 'open'), alert('a2', 'acknowledged'), alert('a3', 'open')])
    const r = diffAlerts(known, [alert('a1', 'acknowledged'), alert('a2', 'resolved'), alert('a3', 'cancelled')], false)
    expect(r.changes.map((c) => c.kind)).toEqual(['acknowledged', 'resolved', 'cancelled'])
  })

  it('does not report a new alert that is already closed', () => {
    const r = diffAlerts(knownAlerts([]), [alert('a1', 'resolved')], false)
    expect(r.changes).toEqual([])
  })

  it('reports an alert that leaves an open-only list as closed, once', () => {
    const known = knownAlerts([alert('a1', 'open'), alert('a2', 'open')])
    const r = diffAlerts(known, [alert('a2', 'open')], true)
    expect(r.changes.map((c) => [c.kind, c.alert.alert_id])).toEqual([['closed', 'a1']])
    expect(diffAlerts(r.known, [alert('a2', 'open')], true).changes).toEqual([])
  })

  it('ignores alerts missing from a list of every status', () => {
    const r = diffAlerts(knownAlerts([alert('a1', 'open')]), [], false)
    expect(r.changes).toEqual([])
  })
})

describe('overrides', () => {
  const ack = alert('a1', 'acknowledged')
  const resolved = alert('a1', 'resolved')

  it('keeps the change while the server still returns the old status', () => {
    const overrides = new Map([['a1', { bases: new Set(['open']), alert: ack, at: 0 }]])
    expect(applyOverrides([alert('a1', 'open')], overrides)).toEqual([ack])
    expect(pruneOverrides(overrides, [alert('a1', 'open')], 1000)).toBe(overrides)
  })

  it('lets the server win once it has a different status, or after the TTL', () => {
    const overrides = new Map([['a1', { bases: new Set(['open']), alert: ack, at: 0 }]])
    expect(pruneOverrides(overrides, [alert('a1', 'acknowledged')], 1000).size).toBe(0)
    expect(pruneOverrides(overrides, [alert('a1', 'open')], OVERRIDE_TTL_MS).size).toBe(0)
  })

  it('keeps an acknowledge-then-resolve while the server is at either step', () => {
    const overrides = new Map([['a1', { bases: new Set(['open', 'acknowledged']), alert: resolved, at: 0 }]])
    expect(applyOverrides([alert('a1', 'open')], overrides)).toEqual([resolved])
    expect(applyOverrides([alert('a1', 'acknowledged')], overrides)).toEqual([resolved])
  })

  it('keeps a resolved alert the open list dropped, but not an acknowledged one', () => {
    const overrides = new Map([
      ['a1', { bases: new Set(['open']), alert: resolved, at: 0 }],
      ['a2', { bases: new Set(['open']), alert: alert('a2', 'acknowledged'), at: 0 }],
    ])
    const pruned = pruneOverrides(overrides, [], 1000)
    expect([...pruned.keys()]).toEqual(['a1'])
    expect(applyOverrides([], pruned)).toEqual([resolved])
  })

  it('returns the server list itself when there are no overrides', () => {
    const server = [alert('a1', 'open')]
    expect(applyOverrides(server, new Map())).toBe(server)
  })
})
