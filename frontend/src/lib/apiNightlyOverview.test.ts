/**
 * `getNightlyOverview` (docs/features/nightly-overview.md, criterion 18): the request and
 * the tab's 10-minute in-memory reuse. `./auth` is mocked and `fetch` is stubbed, so
 * nothing reaches Cognito or the real API. Only `Date` is faked, so promises run normally.
 */
import { deferred } from '../test/fixtures'
import { testFlagged, testOverview } from '../test/nightlyFixtures'

const auth = vi.hoisted(() => ({ getApiToken: vi.fn<() => Promise<string | null>>() }))
vi.mock('./auth', () => ({ getApiToken: auth.getApiToken }))

import { ApiError, NIGHTLY_OVERVIEW_REUSE_MS, clearNightlyOverviewCache, getNightlyOverview } from './api'

const TEST_TOKEN = 'test-id-token-overview'
const BASE = 'https://api.example.test'
const fetchMock = vi.fn<typeof fetch>()

/** 10:00 SGT on 19 Nov 2026. */
const T0 = new Date('2026-11-19T02:00:00Z')
const MINUTE = 60_000

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

async function apiErrorOf(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => {
      throw new Error('expected the request to fail')
    },
    (e: unknown) => e,
  )
  expect(error).toBeInstanceOf(ApiError)
  return error as ApiError
}

const consoleMethods = ['log', 'info', 'warn', 'error', 'debug', 'trace'] as const
let consoleSpies: ReturnType<typeof vi.spyOn>[] = []

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(T0)
  clearNightlyOverviewCache()
  vi.stubEnv('VITE_API_URL', `${BASE}/`)
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  fetchMock.mockImplementation(async () => jsonResponse(testOverview()))
  auth.getApiToken.mockReset()
  auth.getApiToken.mockResolvedValue(TEST_TOKEN)
  consoleSpies = consoleMethods.map((m) => vi.spyOn(console, m).mockImplementation(() => {}))
})

afterEach(() => {
  // Nothing (resident IDs, flags, thresholds) is logged.
  for (const spy of consoleSpies) {
    expect(spy).not.toHaveBeenCalled()
    spy.mockRestore()
  }
  clearNightlyOverviewCache()
  vi.useRealTimers()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('getNightlyOverview request', () => {
  it('GETs /nightly/overview with the ID token and no query string, and resolves to the body', async () => {
    const body = testOverview()
    fetchMock.mockResolvedValue(jsonResponse(body))
    await expect(getNightlyOverview()).resolves.toEqual(body)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://api.example.test/nightly/overview')
    expect(init?.method).toBe('GET')
    const headers = init?.headers as Record<string, string> | undefined
    expect(headers?.Authorization).toBe(`Bearer ${TEST_TOKEN}`)
    expect(init?.body).toBeUndefined()
  })

  it('accepts a not-ready body with an empty list', async () => {
    const body = testOverview({ available: false, not_ready: true, flagged: [], clear_count: 0, no_summary_count: 0 })
    fetchMock.mockResolvedValue(jsonResponse(body))
    await expect(getNightlyOverview()).resolves.toEqual(body)
  })

  it('accepts a flagged entry whose thresholds are null (limits unreadable)', async () => {
    const body = testOverview({ flagged: [testFlagged({ falls: 1, thresholds: null })] })
    fetchMock.mockResolvedValue(jsonResponse(body))
    await expect(getNightlyOverview()).resolves.toEqual(body)
  })

  it.each([
    ['not an object', 'nope'],
    ['missing expected_date', { ...testOverview(), expected_date: undefined }],
    ['available not boolean', { ...testOverview(), available: 'yes' }],
    ['flagged not an array', { ...testOverview(), flagged: {} }],
    ['an entry with thresholds not an object', { ...testOverview(), flagged: [{ ...testFlagged(), thresholds: 'x' }] }],
    ['an entry without thresholds', { ...testOverview(), flagged: [{ ...testFlagged(), thresholds: undefined }] }],
    ['clear_count not a number', { ...testOverview(), clear_count: '14' }],
  ])('rejects a body with %s as invalid_response', async (_label, body) => {
    fetchMock.mockResolvedValue(jsonResponse(body))
    const error = await apiErrorOf(getNightlyOverview())
    expect(error.code).toBe('invalid_response')
  })

  it.each([
    [403, 'forbidden'],
    [429, 'too_many_requests'],
    [500, 'internal_error'],
  ])('turns a %i into an ApiError', async (status, code) => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code, message: 'x' } }, status))
    const error = await apiErrorOf(getNightlyOverview())
    expect(error.status).toBe(status)
    expect(error.code).toBe(code)
  })
})

describe('getNightlyOverview reuse (10 minutes, same Singapore date)', () => {
  it('is 10 minutes', () => {
    expect(NIGHTLY_OVERVIEW_REUSE_MS).toBe(10 * MINUTE)
  })

  it('reuses the answer within 10 minutes and loads again after', async () => {
    await getNightlyOverview()
    vi.setSystemTime(T0.getTime() + 10 * MINUTE - 1)
    await getNightlyOverview()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    vi.setSystemTime(T0.getTime() + 10 * MINUTE)
    await getNightlyOverview()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('reuses a not-ready answer the same way', async () => {
    const notReady = testOverview({ available: false, not_ready: false, flagged: [], clear_count: 0, no_summary_count: 0 })
    fetchMock.mockResolvedValueOnce(jsonResponse(notReady))
    await expect(getNightlyOverview()).resolves.toEqual(notReady)
    vi.setSystemTime(T0.getTime() + 9 * MINUTE)
    await expect(getNightlyOverview()).resolves.toEqual(notReady)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    vi.setSystemTime(T0.getTime() + 10 * MINUTE)
    await expect(getNightlyOverview()).resolves.toEqual(testOverview())
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('shares a request still in flight', async () => {
    const response = deferred<Response>()
    fetchMock.mockReturnValueOnce(response.promise)
    const first = getNightlyOverview()
    const second = getNightlyOverview()
    response.resolve(jsonResponse(testOverview()))
    await expect(first).resolves.toEqual(testOverview())
    await expect(second).resolves.toEqual(testOverview())
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('counts the 10 minutes from when the answer arrived', async () => {
    const response = deferred<Response>()
    fetchMock.mockReturnValueOnce(response.promise)
    const first = getNightlyOverview()
    vi.setSystemTime(T0.getTime() + 3 * MINUTE)
    response.resolve(jsonResponse(testOverview()))
    await first
    // 12 minutes after the request, 9 after the answer: still reused.
    vi.setSystemTime(T0.getTime() + 12 * MINUTE)
    await getNightlyOverview()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.setSystemTime(T0.getTime() + 13 * MINUTE)
    await getNightlyOverview()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('loads again when the Singapore date has changed, even within 10 minutes', async () => {
    // 23:58 SGT on 19 Nov, then 00:01 SGT on 20 Nov.
    vi.setSystemTime(new Date('2026-11-19T15:58:00Z'))
    await getNightlyOverview()
    vi.setSystemTime(new Date('2026-11-19T15:59:59Z'))
    await getNightlyOverview()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.setSystemTime(new Date('2026-11-19T16:01:00Z'))
    await getNightlyOverview()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('loads again if the clock went backwards', async () => {
    await getNightlyOverview()
    vi.setSystemTime(T0.getTime() - MINUTE)
    await getNightlyOverview()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('skips the copy with fresh: true ("Try again") and keeps the new answer', async () => {
    await getNightlyOverview()
    const newer = testOverview({ clear_count: 15, flagged: [testFlagged({ resident_id: 'res-0002', falls: 1 })] })
    fetchMock.mockResolvedValueOnce(jsonResponse(newer))
    await expect(getNightlyOverview({ fresh: true })).resolves.toEqual(newer)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await expect(getNightlyOverview()).resolves.toEqual(newer)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("doesn't keep a failed request", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: 'too_many_requests', message: 'x' } }, 429))
    await apiErrorOf(getNightlyOverview())
    await expect(getNightlyOverview()).resolves.toEqual(testOverview())
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("doesn't keep an invalid body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ nope: true }))
    await apiErrorOf(getNightlyOverview())
    await expect(getNightlyOverview()).resolves.toEqual(testOverview())
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('keeps the copy in memory only, never in browser storage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    await getNightlyOverview()
    expect(setItem).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    setItem.mockRestore()
  })
})
