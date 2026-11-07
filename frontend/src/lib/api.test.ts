/**
 * Unit tests for the typed API client. `./auth` (getApiToken) is mocked and `fetch` is
 * stubbed, so nothing reaches Cognito or the real API.
 */
import type { ApiDashboardResponse } from '../types/dashboard'
import type { ApiDevice } from '../types/devices'

const auth = vi.hoisted(() => ({ getApiToken: vi.fn<() => Promise<string | null>>() }))
vi.mock('./auth', () => ({ getApiToken: auth.getApiToken }))

import { ApiError, apiGet, getDashboard, getDevices } from './api'

const TEST_TOKEN = 'test-id-token-abc123'
const BASE = 'https://api.example.test'

const fetchMock = vi.fn<typeof fetch>()

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function textResponse(body: string, status: number): Response {
  return new Response(body, { status, headers: { 'Content-Type': 'text/html' } })
}

/** Resolves to the ApiError the promise rejects with (fails the test if it resolves). */
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
  vi.stubEnv('VITE_API_URL', `${BASE}/`)
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  auth.getApiToken.mockReset()
  auth.getApiToken.mockResolvedValue(TEST_TOKEN)
  consoleSpies = consoleMethods.map((m) => vi.spyOn(console, m).mockImplementation(() => {}))
})

afterEach(() => {
  for (const spy of consoleSpies) {
    expect(spy).not.toHaveBeenCalled()
    spy.mockRestore()
  }
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('ApiError', () => {
  it('is an Error with status, code, and a message that holds only the code', () => {
    const error = new ApiError(403, 'forbidden')
    expect(error).toBeInstanceOf(Error)
    expect(error.name).toBe('ApiError')
    expect(error.status).toBe(403)
    expect(error.code).toBe('forbidden')
    expect(error.message).toBe('API request failed (forbidden)')
  })
})

describe('apiGet request', () => {
  it('sends a GET to the base URL + path, with the ID token and no cookies or cache', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))
    await expect(apiGet('/devices')).resolves.toEqual({ ok: true })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://api.example.test/devices')
    expect(init).toEqual({
      method: 'GET',
      headers: { Authorization: `Bearer ${TEST_TOKEN}`, Accept: 'application/json' },
      cache: 'no-store',
      credentials: 'omit',
    })
    expect(auth.getApiToken).toHaveBeenCalledTimes(1)
  })

  it('strips every trailing slash from the base URL (no double slash)', async () => {
    vi.stubEnv('VITE_API_URL', `${BASE}///`)
    fetchMock.mockResolvedValue(jsonResponse({}))
    await apiGet('/devices')
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toBe('https://api.example.test/devices')
    expect(url.replace('https://', '')).not.toContain('//')
  })

  it('works with a base URL that has no trailing slash', async () => {
    vi.stubEnv('VITE_API_URL', BASE)
    fetchMock.mockResolvedValue(jsonResponse({}))
    await apiGet('/devices')
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/devices')
  })

  it('never puts the token in the URL', async () => {
    fetchMock.mockResolvedValue(jsonResponse({}))
    await apiGet('/devices')
    const url = String(fetchMock.mock.calls[0][0])
    expect(url).not.toContain(TEST_TOKEN)
    expect(url).not.toContain('token')
    expect(url).not.toContain('?')
  })

  it('resolves to the parsed JSON body of a 2xx response', async () => {
    const body = { devices: [], extra: { nested: [1, 2] } }
    fetchMock.mockResolvedValue(jsonResponse(body, 200))
    await expect(apiGet('/devices')).resolves.toEqual(body)
  })
})

describe('apiGet errors', () => {
  it('not_configured (status 0) when VITE_API_URL is empty, without asking for a token', async () => {
    vi.stubEnv('VITE_API_URL', '')
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 0, code: 'not_configured' })
    expect(fetchMock).not.toHaveBeenCalled()
    expect(auth.getApiToken).not.toHaveBeenCalled()
  })

  it('not_configured when VITE_API_URL is only slashes', async () => {
    vi.stubEnv('VITE_API_URL', '///')
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 0, code: 'not_configured' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('not_configured when VITE_API_URL is unset', async () => {
    vi.stubEnv('VITE_API_URL', undefined)
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 0, code: 'not_configured' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('unauthenticated (401) when there is no token, and fetch is not called', async () => {
    auth.getApiToken.mockResolvedValue(null)
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 401, code: 'unauthenticated' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('unauthenticated when the token is an empty string', async () => {
    auth.getApiToken.mockResolvedValue('')
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 401, code: 'unauthenticated' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('network_error (status 0) when fetch rejects', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 0, code: 'network_error' })
    expect(error.message).not.toContain('Failed to fetch')
  })

  it("uses the API's error.code when it is a valid code", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code: 'forbidden', message: 'No access' } }, 403))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 403, code: 'forbidden' })
  })

  it.each([
    ['a', 'a'],
    ['digits and underscores', 'rate_limited_2'],
    ['64 characters', `a${'b'.repeat(63)}`],
  ])('accepts a valid code (%s)', async (_label, code) => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code } }, 400))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 400, code })
  })

  it.each([
    ['uppercase', 'Forbidden'],
    ['a leading digit', '1bad'],
    ['a leading underscore', '_bad'],
    ['a hyphen', 'not-allowed'],
    ['a space', 'bad code'],
    ['65 characters', `a${'b'.repeat(64)}`],
    ['a trailing newline', 'forbidden\n'],
    ['markup', '<script>'],
    ['empty', ''],
  ])('falls back to http_<status> for a code with %s', async (_label, code) => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code } }, 403))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 403, code: 'http_403' })
  })

  it.each([
    ['a number code', { error: { code: 42 } }],
    ['a null code', { error: { code: null } }],
    ['no code', { error: { message: 'Something' } }],
    ['a string error', { error: 'forbidden' }],
    ['a null error', { error: null }],
    ['no error key', { message: 'Internal Server Error' }],
    ['a JSON null body', null],
    ['a JSON array body', ['forbidden']],
  ])('falls back to http_<status> for an error body with %s', async (_label, body) => {
    fetchMock.mockResolvedValue(jsonResponse(body, 500))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 500, code: 'http_500' })
  })

  it('falls back to http_<status> when the error body is not JSON', async () => {
    fetchMock.mockResolvedValue(textResponse('<html>Bad Gateway</html>', 502))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 502, code: 'http_502' })
  })

  it('falls back to http_<status> when the error body is empty', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 503 }))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 503, code: 'http_503' })
  })

  it('invalid_response when a 2xx body is not JSON', async () => {
    fetchMock.mockResolvedValue(textResponse('<html>OK</html>', 200))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 200, code: 'invalid_response' })
  })

  it('invalid_response when a 2xx body is empty', async () => {
    fetchMock.mockResolvedValue(new Response('', { status: 200 }))
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error).toMatchObject({ status: 200, code: 'invalid_response' })
  })

  it('the error message never contains the token, URL, or response body', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: { code: 'forbidden', message: 'Detail for Test Resident A' } }, 403),
    )
    const error = await apiErrorOf(apiGet('/devices'))
    expect(error.message).not.toContain(TEST_TOKEN)
    expect(error.message).not.toContain('api.example.test')
    expect(error.message).not.toContain('Test Resident A')
  })
})

describe('getDevices', () => {
  const devices: ApiDevice[] = [
    {
      device_id: 'bedroom-test-001',
      device_type: 'bedroom',
      online: true,
      last_seen: '2026-11-07T02:00:00Z',
      battery_pct: null,
      charging: false,
      fw_version: '1.0.0',
      assigned_to: { resident_id: 'resident-test-001', display_name: 'Test Resident A', home_id: 'home-test-001' },
    },
    {
      device_id: 'wearable-test-spare-001',
      device_type: 'wearable',
      online: false,
      last_seen: null,
      battery_pct: null,
      charging: false,
      fw_version: null,
      assigned_to: null,
    },
  ]

  it('GETs /devices and resolves to the devices array', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ devices }))
    await expect(getDevices()).resolves.toEqual(devices)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/devices')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET' })
  })

  it('resolves to an empty array for an empty fleet', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ devices: [] }))
    await expect(getDevices()).resolves.toEqual([])
  })

  it.each([
    ['no devices key', { items: devices }],
    ['devices not an array', { devices: 'bedroom-test-001' }],
    ['devices null', { devices: null }],
    ['devices an object', { devices: { 0: devices[0] } }],
    ['a top-level array', devices],
    ['a JSON null body', null],
    ['a string body', 'devices'],
  ])('throws ApiError(200, invalid_response) for a body with %s', async (_label, body) => {
    fetchMock.mockResolvedValue(jsonResponse(body))
    const error = await apiErrorOf(getDevices())
    expect(error).toMatchObject({ status: 200, code: 'invalid_response' })
  })

  it("passes through the API's error (e.g. 403 forbidden for a non-staff user)", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code: 'forbidden', message: 'Staff only' } }, 403))
    const error = await apiErrorOf(getDevices())
    expect(error).toMatchObject({ status: 403, code: 'forbidden' })
  })

  it('passes through unauthenticated without calling fetch', async () => {
    auth.getApiToken.mockResolvedValue(null)
    const error = await apiErrorOf(getDevices())
    expect(error).toMatchObject({ status: 401, code: 'unauthenticated' })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('getDashboard', () => {
  const dashboard: ApiDashboardResponse = {
    residents: [
      {
        resident_id: 'resident-test-001',
        display_name: 'Test Resident A',
        home_id: 'home-test-001',
        status: 'attention',
        status_text: 'Synthetic status text.',
        wearable: {
          device_id: 'wearable-test-001',
          ts: '2026-11-07T02:00:00Z',
          battery_pct: 64,
          worn: true,
          charging: false,
          online: true,
        },
        bedroom: { device_id: 'bedroom-test-001', ts: '2026-11-07T02:00:00Z', online: true },
        vitals: [
          { ts: '2026-11-07T02:00:00Z', hr_bpm: 72, spo2_pct: 97, skin_temp_c: 33.1 },
        ],
      },
      {
        resident_id: 'resident-test-002',
        display_name: 'Test Resident B',
        home_id: null,
        status: 'ok',
        status_text: 'Synthetic status text.',
        wearable: null,
        bedroom: null,
        vitals: [],
      },
    ],
    alerts: [
      {
        alert_id: 'alert-test-001',
        resident_id: 'resident-test-001',
        device_id: 'wearable-test-001',
        alert_type: 'spo2_low',
        severity: 'warning',
        status: 'open',
        event_ts: '2026-11-07T02:00:00Z',
        acknowledged_at: null,
        acknowledged_by_role: null,
        acknowledged_by: null,
        ack_note: null,
        escalated_at: null,
        escalates_at: '2026-11-07T02:05:00Z',
      },
    ],
  }

  it('GETs /dashboard with the ID token and resolves to the body as sent', async () => {
    fetchMock.mockResolvedValue(jsonResponse(dashboard))
    await expect(getDashboard()).resolves.toEqual(dashboard)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://api.example.test/dashboard')
    expect(init).toMatchObject({
      method: 'GET',
      headers: { Authorization: `Bearer ${TEST_TOKEN}`, Accept: 'application/json' },
    })
    expect(String(url)).not.toContain(TEST_TOKEN)
    expect(auth.getApiToken).toHaveBeenCalledTimes(1)
  })

  it('resolves to empty lists for no residents and no alerts', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ residents: [], alerts: [] }))
    await expect(getDashboard()).resolves.toEqual({ residents: [], alerts: [] })
  })

  it.each([
    ['no residents key', { alerts: [] }],
    ['no alerts key', { residents: [] }],
    ['residents not an array', { residents: 'resident-test-001', alerts: [] }],
    ['alerts not an array', { residents: [], alerts: { 0: dashboard.alerts[0] } }],
    ['residents null', { residents: null, alerts: [] }],
    ['alerts null', { residents: [], alerts: null }],
    ['an empty object', {}],
    ['a top-level array', dashboard.residents],
    ['a JSON null body', null],
    ['a string body', 'dashboard'],
  ])('throws ApiError(200, invalid_response) for a body with %s', async (_label, body) => {
    fetchMock.mockResolvedValue(jsonResponse(body))
    const error = await apiErrorOf(getDashboard())
    expect(error).toMatchObject({ status: 200, code: 'invalid_response' })
  })

  it("passes through the API's error code (e.g. 403 forbidden for a caregiver)", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code: 'forbidden', message: 'Staff only' } }, 403))
    const error = await apiErrorOf(getDashboard())
    expect(error).toMatchObject({ status: 403, code: 'forbidden' })
  })

  it('falls back to http_<status> for a non-JSON error page', async () => {
    fetchMock.mockResolvedValue(textResponse('<html>Bad Gateway</html>', 502))
    const error = await apiErrorOf(getDashboard())
    expect(error).toMatchObject({ status: 502, code: 'http_502' })
  })

  it('passes through unauthenticated without calling fetch', async () => {
    auth.getApiToken.mockResolvedValue(null)
    const error = await apiErrorOf(getDashboard())
    expect(error).toMatchObject({ status: 401, code: 'unauthenticated' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('passes through not_configured without asking for a token', async () => {
    vi.stubEnv('VITE_API_URL', '')
    const error = await apiErrorOf(getDashboard())
    expect(error).toMatchObject({ status: 0, code: 'not_configured' })
    expect(auth.getApiToken).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
