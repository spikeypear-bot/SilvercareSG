import { act, renderHook, waitFor } from '@testing-library/react'
import { apiAlert, apiResident } from '../../test/apiDashboard'
import { demoDataFor } from '../../test/dashboardMock'
import { deferred } from '../../test/fixtures'
import type { ApiDashboardResponse, DashboardData, ResidentAlert } from '../../types/dashboard'
import type { ApiMyResidentsResponse } from '../../types/resident'

const api = vi.hoisted(() => ({
  getDashboard: vi.fn<() => Promise<ApiDashboardResponse>>(),
  getMyResidents: vi.fn<() => Promise<ApiMyResidentsResponse>>(),
}))
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, getDashboard: api.getDashboard, getMyResidents: api.getMyResidents }
})

import { ApiError } from '../../lib/api'
import { isOnOpenList } from './stats'
import {
  loadDashboardData,
  toHistoryAlert,
  toLinkedResident,
  toResident,
  toResidentAlert,
  useDashboardData,
  type DashboardDataState,
  type DashboardLoader,
} from './useDashboardData'

beforeEach(() => {
  api.getDashboard.mockReset()
  api.getDashboard.mockRejectedValue(new Error('getDashboard not set up for this test'))
  api.getMyResidents.mockReset()
  api.getMyResidents.mockRejectedValue(new Error('getMyResidents not set up for this test'))
})

const sortedKeys = (o: object) => Object.keys(o).sort()

const RESIDENT_KEYS = ['bedroom', 'home_id', 'name', 'resident_id', 'status', 'status_text', 'vitals', 'wearable']
const ALERT_REQUIRED_KEYS = ['alert_id', 'alert_type', 'device_id', 'event_ts', 'resident_id', 'severity', 'status']

describe('toResident', () => {
  it('copies each field, with display_name as name (exact output keys)', () => {
    const resident = toResident(apiResident(1, { status: 'attention', status_text: 'Synthetic attention text.' }))
    expect(resident).toEqual({
      resident_id: 'resident-test-001',
      name: 'Test Resident A',
      home_id: 'home-test-001',
      status: 'attention',
      status_text: 'Synthetic attention text.',
      wearable: {
        device_id: 'wearable-test-001',
        ts: '2026-11-07T02:00:00Z',
        battery_pct: 80,
        worn: true,
        charging: false,
        online: true,
      },
      bedroom: { device_id: 'bedroom-test-001', ts: '2026-11-07T02:00:00Z', online: true },
      vitals: [{ ts: '2026-11-07T02:00:00Z', hr_bpm: 72, spo2_pct: 97, skin_temp_c: 33.2, activity_pct: null }],
    })
    expect(sortedKeys(resident)).toEqual(RESIDENT_KEYS)
    expect(resident).not.toHaveProperty('display_name')
    expect(sortedKeys(resident.wearable!)).toEqual(['battery_pct', 'charging', 'device_id', 'online', 'ts', 'worn'])
    expect(sortedKeys(resident.bedroom!)).toEqual(['device_id', 'online', 'ts'])
    expect(sortedKeys(resident.vitals[0])).toEqual(['activity_pct', 'hr_bpm', 'skin_temp_c', 'spo2_pct', 'ts'])
  })

  it('does not pass through a field the API adds later, at any level', () => {
    const base = apiResident(1)
    const withExtras = {
      ...base,
      internal_sub: 'sub-test-should-not-pass',
      wearable: { ...base.wearable!, fw_version: '9.9.9' },
      bedroom: { ...base.bedroom!, battery_pct: 50 },
      vitals: [{ ...base.vitals[0], steps: 1234 }],
    }
    const resident = toResident(withExtras)
    expect(sortedKeys(resident)).toEqual(RESIDENT_KEYS)
    expect(JSON.stringify(resident)).not.toMatch(/internal_sub|sub-test|fw_version|steps/)
    expect(resident.bedroom).not.toHaveProperty('battery_pct')
    expect(resident.wearable).not.toHaveProperty('fw_version')
    expect(resident.vitals[0]).not.toHaveProperty('steps')
  })

  // GET /dashboard no longer sends activity_pct (privacy review); the views' type keeps
  // the key, so the mapper sets it to null on every reading.
  it('sets activity_pct to null on every reading, even if an API still sends a value', () => {
    const base = apiResident(1)
    expect(base.vitals[0]).not.toHaveProperty('activity_pct')
    const stale = {
      ...base,
      vitals: [
        { ...base.vitals[0], activity_pct: 55 },
        { ...base.vitals[0], ts: '2026-11-07T01:59:00Z' },
      ],
    }
    const resident = toResident(stale)
    expect(resident.vitals).toHaveLength(2)
    for (const reading of resident.vitals) expect(reading).toHaveProperty('activity_pct', null)
    expect(JSON.stringify(resident)).not.toContain('55')
  })

  it("maps a null home_id to ''", () => {
    expect(toResident(apiResident(1, { home_id: null })).home_id).toBe('')
  })

  it('keeps a null wearable and a null bedroom as null (not missing)', () => {
    const resident = toResident(apiResident(2, { wearable: null, bedroom: null, vitals: [] }))
    expect(resident).toHaveProperty('wearable', null)
    expect(resident).toHaveProperty('bedroom', null)
    expect(resident.vitals).toEqual([])
  })

  it('keeps never-reported device values and a null activity as null', () => {
    const resident = toResident(
      apiResident(3, {
        wearable: { ts: null, battery_pct: null, worn: null, online: false },
        bedroom: { ts: null, online: false },
        vitals: [{ ts: '2026-11-07T02:00:00Z', hr_bpm: null, spo2_pct: null, skin_temp_c: null }],
      }),
    )
    expect(resident.wearable).toEqual({
      device_id: 'wearable-test-003',
      ts: null,
      battery_pct: null,
      worn: null,
      charging: false,
      online: false,
    })
    expect(resident.bedroom).toEqual({ device_id: 'bedroom-test-003', ts: null, online: false })
    expect(resident.vitals[0]).toEqual({
      ts: '2026-11-07T02:00:00Z',
      hr_bpm: null,
      spo2_pct: null,
      skin_temp_c: null,
      activity_pct: null,
    })
  })

  it('returns new objects, not the API ones', () => {
    const source = apiResident(1)
    const resident = toResident(source)
    expect(resident.wearable).not.toBe(source.wearable)
    expect(resident.bedroom).not.toBe(source.bedroom)
    expect(resident.vitals).not.toBe(source.vitals)
  })
})

describe('toResidentAlert', () => {
  it('drops every null optional value: only the required keys are left', () => {
    const alert = toResidentAlert(apiAlert())
    expect(alert).toEqual({
      alert_id: 'alert-test-001',
      resident_id: 'resident-test-001',
      device_id: 'wearable-test-001',
      alert_type: 'fall',
      severity: 'critical',
      status: 'open',
      event_ts: '2026-11-07T02:00:00Z',
    })
    expect(sortedKeys(alert)).toEqual(ALERT_REQUIRED_KEYS)
    for (const key of ['acknowledged_at', 'acknowledged_by_role', 'acknowledged_by', 'ack_note', 'escalated_at', 'escalates_at']) {
      expect(alert).not.toHaveProperty(key)
    }
  })

  it("keeps a nurse's acknowledge with its note and escalation time", () => {
    const alert = toResidentAlert(
      apiAlert({
        status: 'acknowledged',
        acknowledged_at: '2026-11-07T02:01:00Z',
        ack_note: 'Synthetic note: on the way.',
        escalated_at: '2026-11-07T02:00:01Z',
      }),
    )
    expect(alert).toEqual({
      alert_id: 'alert-test-001',
      resident_id: 'resident-test-001',
      device_id: 'wearable-test-001',
      alert_type: 'fall',
      severity: 'critical',
      status: 'acknowledged',
      event_ts: '2026-11-07T02:00:00Z',
      acknowledged_at: '2026-11-07T02:01:00Z',
      ack_note: 'Synthetic note: on the way.',
      escalated_at: '2026-11-07T02:00:01Z',
    })
    expect(alert).not.toHaveProperty('acknowledged_by_role')
    expect(alert).not.toHaveProperty('acknowledged_by')
  })

  it("keeps a caregiver's acknowledge of a device problem, with role and name", () => {
    const alert = toResidentAlert(
      apiAlert({
        alert_type: 'battery_low',
        severity: 'warning',
        status: 'acknowledged',
        acknowledged_at: '2026-11-07T02:01:00Z',
        acknowledged_by_role: 'caregiver',
        acknowledged_by: 'Test Caregiver A',
      }),
    )
    expect(alert).toMatchObject({
      status: 'acknowledged',
      acknowledged_at: '2026-11-07T02:01:00Z',
      acknowledged_by_role: 'caregiver',
      acknowledged_by: 'Test Caregiver A',
    })
    expect(sortedKeys(alert)).toEqual(
      [...ALERT_REQUIRED_KEYS, 'acknowledged_at', 'acknowledged_by', 'acknowledged_by_role'].sort(),
    )
  })

  it('keeps escalates_at on an open warning', () => {
    const alert = toResidentAlert(
      apiAlert({ alert_type: 'spo2_low', severity: 'warning', escalates_at: '2026-11-07T02:05:00Z' }),
    )
    expect(alert.escalates_at).toBe('2026-11-07T02:05:00Z')
    expect(sortedKeys(alert)).toEqual([...ALERT_REQUIRED_KEYS, 'escalates_at'].sort())
  })

  it('does not pass through a field the API adds later', () => {
    const withExtras = { ...apiAlert(), acknowledged_by_sub: 'sub-test-should-not-pass', resolved_by: 'Test Nurse A' }
    const alert = toResidentAlert(withExtras)
    expect(sortedKeys(alert)).toEqual(ALERT_REQUIRED_KEYS)
    expect(JSON.stringify(alert)).not.toMatch(/sub-test|resolved_by/)
  })
})

describe('loadDashboardData', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('staff: GET /dashboard mapped through toResident and toResidentAlert', async () => {
    const body: ApiDashboardResponse = {
      residents: [apiResident(1), apiResident(2, { wearable: null, bedroom: null, home_id: null, vitals: [] })],
      alerts: [
        apiAlert({ alert_type: 'spo2_low', severity: 'warning', escalates_at: '2026-11-07T02:05:00Z' }),
        apiAlert({ alert_id: 'alert-test-002', acknowledged_at: '2026-11-07T02:01:00Z', status: 'acknowledged' }),
      ],
    }
    api.getDashboard.mockResolvedValue(body)
    const data = await loadDashboardData('staff')
    expect(api.getDashboard).toHaveBeenCalledTimes(1)
    expect(data).toEqual({
      residents: body.residents.map(toResident),
      alerts: body.alerts.map(toResidentAlert),
    })
    expect(sortedKeys(data)).toEqual(['alerts', 'residents'])
    expect(data.residents[1]).toMatchObject({ name: 'Test Resident B', home_id: '', wearable: null, bedroom: null })
    // No demo data mixed in.
    expect(data.residents.map((r) => r.resident_id)).toEqual(['resident-test-001', 'resident-test-002'])
  })

  it('staff: no demo delay, it settles as soon as the API answers', async () => {
    vi.useFakeTimers()
    api.getDashboard.mockResolvedValue({ residents: [], alerts: [] })
    let settled = false
    void loadDashboardData('staff').then(() => {
      settled = true
    })
    // Flush microtasks only: no timer is advanced.
    await vi.advanceTimersByTimeAsync(0)
    expect(settled).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('staff: keeps the order the API sends (newest first for alerts)', async () => {
    api.getDashboard.mockResolvedValue({
      residents: [apiResident(2), apiResident(1)],
      alerts: [
        apiAlert({ alert_id: 'alert-test-002', event_ts: '2026-11-07T02:10:00Z' }),
        apiAlert({ alert_id: 'alert-test-001', event_ts: '2026-11-07T02:00:00Z' }),
      ],
    })
    const data = await loadDashboardData('staff')
    expect(data.residents.map((r) => r.resident_id)).toEqual(['resident-test-002', 'resident-test-001'])
    expect(data.alerts.map((a) => a.alert_id)).toEqual(['alert-test-002', 'alert-test-001'])
  })

  it('staff: rejects with the ApiError from getDashboard', async () => {
    const error = new ApiError(403, 'forbidden')
    api.getDashboard.mockRejectedValue(error)
    await expect(loadDashboardData('staff')).rejects.toBe(error)
  })

  it('family: GET /me/residents, keeping activity_pct and the resolve and cancel fields', async () => {
    const reading = { ts: '2026-11-07T02:00:00.000Z', hr_bpm: 72, spo2_pct: 97, skin_temp_c: 33.4, activity_pct: 12 }
    const resident = { ...apiResident(1), vitals: [reading] }
    const alert = {
      ...apiAlert(),
      status: 'cancelled' as const,
      resolved_at: null,
      resolved_by: null,
      cancelled_at: '2026-11-07T02:05:00.000Z',
      cancelled_by_role: 'caregiver' as const,
      cancelled_by: 'Test Caregiver A',
    }
    api.getMyResidents.mockResolvedValue({ residents: [resident], alerts: [alert] })
    const data = await loadDashboardData('family')
    expect(data).toEqual({ residents: [toLinkedResident(resident)], alerts: [toHistoryAlert(alert)] })
    expect(data.residents[0].vitals[0].activity_pct).toBe(12)
    expect(data.alerts[0]).toMatchObject({ status: 'cancelled', cancelled_by: 'Test Caregiver A' })
    expect(data.alerts[0]).not.toHaveProperty('resolved_by')
    expect(api.getDashboard).not.toHaveBeenCalled()
  })

  it('family: rejects with the ApiError from getMyResidents', async () => {
    const error = new ApiError(403, 'forbidden')
    api.getMyResidents.mockRejectedValue(error)
    await expect(loadDashboardData('family')).rejects.toBe(error)
  })
})

describe('useDashboardData with the default loader', () => {
  it('staff: loading, then the mapped GET /dashboard data', async () => {
    const pending = deferred<ApiDashboardResponse>()
    api.getDashboard.mockReturnValue(pending.promise)
    const { result } = renderHook(() => useDashboardData('staff'))
    expect(result.current).toMatchObject({ loading: true, error: false, data: null })

    const body = { residents: [apiResident(1)], alerts: [apiAlert()] }
    await act(async () => pending.resolve(body))
    expect(result.current).toMatchObject({
      loading: false,
      error: false,
      data: { residents: body.residents.map(toResident), alerts: body.alerts.map(toResidentAlert) },
    })
  })

  it('staff: an API error shows the error state, and retry calls the API again', async () => {
    api.getDashboard.mockRejectedValueOnce(new ApiError(0, 'network_error'))
    api.getDashboard.mockResolvedValueOnce({ residents: [apiResident(1)], alerts: [] })
    const { result } = renderHook(() => useDashboardData('staff'))
    await waitFor(() => expect(result.current.error).toBe(true))
    expect(result.current).toMatchObject({ loading: false, data: null })

    act(() => result.current.retry())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe(false)
    expect(result.current.data?.residents.map((r) => r.name)).toEqual(['Test Resident A'])
    expect(api.getDashboard).toHaveBeenCalledTimes(2)
  })
})

describe('useDashboardData', () => {
  it('is loading, then has data', async () => {
    const pending = deferred<DashboardData>()
    const loader = vi.fn<DashboardLoader>(() => pending.promise)
    const { result } = renderHook(() => useDashboardData('staff', loader))
    expect(result.current).toMatchObject({ loading: true, error: false, data: null })

    const data = demoDataFor('staff')
    await act(async () => pending.resolve(data))
    expect(result.current).toMatchObject({ loading: false, error: false, data })
    expect(loader).toHaveBeenCalledWith('staff')
  })

  it('reports an error and loads again on retry', async () => {
    const loader = vi.fn<DashboardLoader>().mockRejectedValueOnce(new Error('boom'))
    loader.mockResolvedValue(demoDataFor('family'))
    const { result } = renderHook(() => useDashboardData('family', loader))
    await waitFor(() => expect(result.current.error).toBe(true))
    expect(result.current.loading).toBe(false)

    act(() => result.current.retry())
    expect(result.current).toMatchObject({ loading: true, error: false })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe(false)
    expect(result.current.data?.residents).toHaveLength(2)
    expect(loader).toHaveBeenCalledTimes(2)
  })
})

describe('acknowledgeAlert and resolveAlert (local state only)', () => {
  const NOW = new Date('2026-10-18T02:30:00Z')
  const ACTION_TIME = new Date('2026-10-18T02:31:00Z')

  afterEach(() => {
    vi.useRealTimers()
  })

  async function loaded() {
    const data = demoDataFor('staff', NOW)
    const loader = vi.fn<DashboardLoader>().mockResolvedValue(data)
    const hook = renderHook(() => useDashboardData('staff', loader))
    await waitFor(() => expect(hook.result.current.data).not.toBeNull())
    // Fake only Date so the actions get a known timestamp; promises and timers stay real.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(ACTION_TIME)
    return { ...hook, data }
  }

  type HookResult = { current: DashboardDataState }
  const alertsOf = (result: HookResult) => result.current.data?.alerts ?? []
  const find = (result: HookResult, id: string) =>
    alertsOf(result).find((a) => a.alert_id === id) as ResidentAlert
  const firstWith = (data: DashboardData, status: ResidentAlert['status']) =>
    data.alerts.find((a) => a.status === status) as ResidentAlert

  it('acknowledge: open -> acknowledged, with at and no name or note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => void result.current.acknowledgeAlert(target.alert_id))
    const after = find(result, target.alert_id)
    expect(after).toEqual({
      ...target,
      status: 'acknowledged',
      acknowledged_at: ACTION_TIME.toISOString(),
    })
    // toEqual ignores undefined keys, so check the absent ones explicitly.
    expect(after).not.toHaveProperty('acknowledged_by')
    expect(after).not.toHaveProperty('ack_note')
  })

  it('acknowledgeAlert takes the alert ID and an optional note, never an actor', async () => {
    const { result } = await loaded()
    expect(result.current.acknowledgeAlert).toHaveLength(2)
  })

  // docs/features/alert-actions-ui.md, criteria 7, 9, 13, 17.
  it('acknowledge with a note stores it as ack_note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => void result.current.acknowledgeAlert(target.alert_id, 'Synthetic note: on the way.\nAbout 8 minutes.'))
    expect(find(result, target.alert_id)).toEqual({
      ...target,
      status: 'acknowledged',
      acknowledged_at: ACTION_TIME.toISOString(),
      ack_note: 'Synthetic note: on the way.\nAbout 8 minutes.',
    })
  })

  it('acknowledge with an empty note stores no ack_note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => void result.current.acknowledgeAlert(target.alert_id, ''))
    expect(find(result, target.alert_id)).not.toHaveProperty('ack_note')
  })

  it('a second acknowledge keeps the first time and note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => void result.current.acknowledgeAlert(target.alert_id, 'Synthetic first note'))
    const first = find(result, target.alert_id)
    vi.setSystemTime(new Date(ACTION_TIME.getTime() + 60_000))
    act(() => void result.current.acknowledgeAlert(target.alert_id, 'Synthetic second note'))
    expect(find(result, target.alert_id)).toBe(first)
  })

  it('cancel: open -> cancelled by a caregiver, with time and display name', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => void result.current.cancelAlert(target.alert_id, 'Test Caregiver A'))
    expect(find(result, target.alert_id)).toEqual({
      ...target,
      status: 'cancelled',
      cancelled_at: ACTION_TIME.toISOString(),
      cancelled_by_role: 'caregiver',
      cancelled_by: 'Test Caregiver A',
    })
  })

  it('cancel of an acknowledged alert keeps the acknowledge and its note', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'acknowledged')
    expect(target.acknowledged_at).toBeDefined()
    act(() => void result.current.cancelAlert(target.alert_id, 'Test Caregiver A'))
    const after = find(result, target.alert_id)
    expect(after.status).toBe('cancelled')
    expect(after.acknowledged_at).toBe(target.acknowledged_at)
    expect(after.ack_note).toBe(target.ack_note)
  })

  it('cancel does nothing to a cancelled or resolved alert', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    const resolved = find(result, target.alert_id)
    act(() => void result.current.cancelAlert(target.alert_id, 'Test Caregiver A'))
    expect(find(result, target.alert_id)).toBe(resolved)

    const other = data.alerts.filter((a) => a.status === 'open')[1]
    act(() => void result.current.cancelAlert(other.alert_id, 'Test Caregiver A'))
    const cancelled = find(result, other.alert_id)
    act(() => void result.current.cancelAlert(other.alert_id, 'Test Caregiver B'))
    expect(find(result, other.alert_id)).toBe(cancelled)
  })

  it('cancel with an empty display name stores no cancelled_by', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => void result.current.cancelAlert(target.alert_id, ''))
    expect(find(result, target.alert_id)).not.toHaveProperty('cancelled_by')
  })

  it('a cancelled alert leaves the open list', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => void result.current.cancelAlert(target.alert_id, 'Test Caregiver A'))
    expect(isOnOpenList(find(result, target.alert_id))).toBe(false)
  })

  const openDeviceProblem = (data: DashboardData) =>
    data.alerts.find((a) => a.status === 'open' && a.alert_type === 'battery_low') as ResidentAlert

  it("the nurse's acknowledge leaves an open device problem unchanged (nurses acknowledge health alerts only)", async () => {
    const { result, data } = await loaded()
    const target = openDeviceProblem(data)
    act(() => void result.current.acknowledgeAlert(target.alert_id))
    expect(find(result, target.alert_id)).toBe(target)
  })

  it('a caregiver acknowledge: open device problem -> acknowledged, with at, role, and the display name', async () => {
    const { result, data } = await loaded()
    const target = openDeviceProblem(data)
    act(() => void result.current.acknowledgeDeviceProblem(target.alert_id, 'Test Caregiver A'))
    const after = find(result, target.alert_id)
    expect(after).toEqual({
      ...target,
      status: 'acknowledged',
      acknowledged_at: ACTION_TIME.toISOString(),
      acknowledged_by_role: 'caregiver',
      acknowledged_by: 'Test Caregiver A',
    })
    expect(after).not.toHaveProperty('ack_note')
  })

  it('a caregiver acknowledge leaves a health alert, or a device problem no longer open, unchanged', async () => {
    const { result, data } = await loaded()
    const health = firstWith(data, 'open')
    const acked = data.alerts.find((a) => a.status === 'acknowledged' && a.alert_type === 'battery_low') as ResidentAlert
    expect(['device_offline', 'battery_low', 'not_worn']).not.toContain(health.alert_type)
    act(() => void result.current.acknowledgeDeviceProblem(health.alert_id, 'Test Caregiver A'))
    act(() => void result.current.acknowledgeDeviceProblem(acked.alert_id, 'Test Caregiver B'))
    expect(find(result, health.alert_id)).toBe(health)
    expect(find(result, acked.alert_id)).toBe(acked)
  })

  it('acknowledge leaves every other alert untouched (same objects)', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => void result.current.acknowledgeAlert(target.alert_id))
    alertsOf(result).forEach((a, i) => {
      if (a.alert_id !== target.alert_id) expect(a).toBe(data.alerts[i])
    })
    expect(result.current.data?.residents).toBe(data.residents)
  })

  it('acknowledge is a no-op on an already-acknowledged alert (keeps the original at and note)', async () => {
    const { result, data } = await loaded()
    const acked = firstWith(data, 'acknowledged')
    expect(acked.ack_note).toBeDefined()
    const originalAt = acked.acknowledged_at
    act(() => void result.current.acknowledgeAlert(acked.alert_id))
    expect(find(result, acked.alert_id)).toBe(acked)
    expect(find(result, acked.alert_id).acknowledged_at).toBe(originalAt)
    expect(find(result, acked.alert_id).acknowledged_at).not.toBe(ACTION_TIME.toISOString())
    expect(find(result, acked.alert_id).ack_note).toBe(acked.ack_note)
  })

  it('acknowledge is a no-op on a resolved alert', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    const resolved = find(result, target.alert_id)
    act(() => void result.current.acknowledgeAlert(target.alert_id))
    expect(find(result, target.alert_id)).toBe(resolved)
    expect(resolved.status).toBe('resolved')
    expect(resolved).not.toHaveProperty('acknowledged_at')
  })

  it('resolve: open -> resolved, with by and at', async () => {
    const { result, data } = await loaded()
    const target = firstWith(data, 'open')
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    expect(find(result, target.alert_id)).toEqual({
      ...target,
      status: 'resolved',
      resolved_by: 'Test Nurse A',
      resolved_at: ACTION_TIME.toISOString(),
    })
  })

  it('resolveAlert takes the alert ID and the display name (display only, never sent)', async () => {
    const { result } = await loaded()
    expect(result.current.resolveAlert).toHaveLength(2)
  })

  it('resolve leaves a device problem unchanged (only the watchdog resolves those)', async () => {
    const { result, data } = await loaded()
    for (const target of data.alerts.filter((a) => ['device_offline', 'battery_low', 'not_worn'].includes(a.alert_type))) {
      act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
      expect(find(result, target.alert_id)).toBe(target)
    }
  })

  it('resolve: acknowledged -> resolved, keeping the acknowledgement and its note', async () => {
    const { result, data } = await loaded()
    const acked = firstWith(data, 'acknowledged')
    expect(acked.ack_note).toBeDefined()
    act(() => result.current.resolveAlert(acked.alert_id, 'Test Nurse A'))
    expect(find(result, acked.alert_id)).toMatchObject({
      status: 'resolved',
      ack_note: acked.ack_note,
      acknowledged_at: acked.acknowledged_at,
      resolved_at: ACTION_TIME.toISOString(),
    })
  })

  it('resolving twice keeps the first resolve time', async () => {
    const { result, data } = await loaded()
    const target = data.alerts[0]
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    const first = find(result, target.alert_id)
    vi.setSystemTime(new Date(ACTION_TIME.getTime() + 60_000))
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    expect(find(result, target.alert_id)).toBe(first)
    expect(first.resolved_at).toBe(ACTION_TIME.toISOString())
  })

  describe('a cancelled alert ("I\'m OK")', () => {
    async function loadedWithCancelled() {
      const data = demoDataFor('staff', NOW)
      const target = firstWith(data, 'open')
      const cancelled: ResidentAlert = {
        ...target,
        status: 'cancelled',
        cancelled_at: NOW.toISOString(),
        cancelled_by_role: 'resident',
      }
      const withCancelled = { ...data, alerts: data.alerts.map((a) => (a === target ? cancelled : a)) }
      const loader = vi.fn<DashboardLoader>().mockResolvedValue(withCancelled)
      const hook = renderHook(() => useDashboardData('staff', loader))
      await waitFor(() => expect(hook.result.current.data).not.toBeNull())
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(ACTION_TIME)
      return { ...hook, data: withCancelled, cancelled }
    }

    it('resolve is a no-op: same object, still cancelled, no resolved_by/at', async () => {
      const { result, cancelled } = await loadedWithCancelled()
      act(() => result.current.resolveAlert(cancelled.alert_id, 'Test Nurse A'))
      const after = find(result, cancelled.alert_id)
      expect(after).toBe(cancelled)
      expect(after.status).toBe('cancelled')
      expect(after).not.toHaveProperty('resolved_by')
      expect(after).not.toHaveProperty('resolved_at')
    })

    it('acknowledge is a no-op too', async () => {
      const { result, cancelled } = await loadedWithCancelled()
      act(() => void result.current.acknowledgeAlert(cancelled.alert_id))
      const after = find(result, cancelled.alert_id)
      expect(after).toBe(cancelled)
      expect(after).not.toHaveProperty('acknowledged_at')
    })

    it('resolving it leaves every other alert untouched (same objects)', async () => {
      const { result, data, cancelled } = await loadedWithCancelled()
      act(() => result.current.resolveAlert(cancelled.alert_id, 'Test Nurse A'))
      alertsOf(result).forEach((a, i) => expect(a).toBe(data.alerts[i]))
    })
  })

  it('resolve leaves every other alert untouched', async () => {
    const { result, data } = await loaded()
    const target = data.alerts[1]
    act(() => result.current.resolveAlert(target.alert_id, 'Test Nurse A'))
    expect(alertsOf(result)).toHaveLength(data.alerts.length)
    alertsOf(result).forEach((a, i) => {
      if (a.alert_id !== target.alert_id) expect(a).toBe(data.alerts[i])
    })
  })

  it('an unknown alert ID changes nothing', async () => {
    const { result, data } = await loaded()
    act(() => void result.current.acknowledgeAlert('alert-test-missing'))
    act(() => result.current.resolveAlert('alert-test-missing', 'Test Nurse A'))
    alertsOf(result).forEach((a, i) => expect(a).toBe(data.alerts[i]))
  })

  it('is a no-op before data loads', async () => {
    const pending = deferred<DashboardData>()
    const loader = vi.fn<DashboardLoader>(() => pending.promise)
    const { result } = renderHook(() => useDashboardData('staff', loader))
    const data = demoDataFor('staff', NOW)
    act(() => void result.current.acknowledgeAlert(data.alerts[0].alert_id))
    act(() => result.current.resolveAlert(data.alerts[0].alert_id, 'Test Nurse A'))
    expect(result.current).toMatchObject({ data: null, loading: true, error: false })

    await act(async () => pending.resolve(data))
    expect(result.current.data).toBe(data)
  })
})
