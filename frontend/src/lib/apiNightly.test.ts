/**
 * `getNightly` (docs/features/nightly-results.md, criterion 25). `./auth` is mocked and
 * `fetch` is stubbed, so nothing reaches Cognito or the real API.
 */
import { testNight, testNightly } from '../test/nightlyFixtures'

const auth = vi.hoisted(() => ({ getApiToken: vi.fn<() => Promise<string | null>>() }))
vi.mock('./auth', () => ({ getApiToken: auth.getApiToken }))

import { ApiError, getNightly } from './api'

const TEST_TOKEN = 'test-id-token-nightly'
const BASE = 'https://api.example.test'
const fetchMock = vi.fn<typeof fetch>()

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
  vi.stubEnv('VITE_API_URL', `${BASE}/`)
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  auth.getApiToken.mockReset()
  auth.getApiToken.mockResolvedValue(TEST_TOKEN)
  consoleSpies = consoleMethods.map((m) => vi.spyOn(console, m).mockImplementation(() => {}))
})

afterEach(() => {
  // Nothing (summary values, thresholds) is logged.
  for (const spy of consoleSpies) {
    expect(spy).not.toHaveBeenCalled()
    spy.mockRestore()
  }
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('getNightly', () => {
  it('GETs /residents/<id>/nightly with the ID token and resolves to the body', async () => {
    const body = testNightly()
    fetchMock.mockResolvedValue(jsonResponse(body))
    await expect(getNightly('res-0003')).resolves.toEqual(body)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://api.example.test/residents/res-0003/nightly')
    expect(init).toMatchObject({
      method: 'GET',
      headers: { Authorization: `Bearer ${TEST_TOKEN}` },
      cache: 'no-store',
      credentials: 'omit',
    })
  })

  it('encodes the resident ID in the path', async () => {
    fetchMock.mockResolvedValue(jsonResponse(testNightly()))
    await getNightly('res 0003/../x')
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/residents/res%200003%2F..%2Fx/nightly')
  })

  it('accepts no nights (empty and not-ready cases)', async () => {
    fetchMock.mockResolvedValue(jsonResponse(testNightly({ nights: [], not_ready: true })))
    await expect(getNightly('res-0003')).resolves.toMatchObject({ nights: [], not_ready: true })
  })

  it.each([
    ['a JSON null body', null],
    ['a top-level array', [testNight()]],
    ['no nights key', { resident_id: 'res-0003', expected_date: '2026-11-18', not_ready: false }],
    ['nights not an array', testNightly({ nights: {} as never })],
    ['not_ready as a string', { ...testNightly(), not_ready: 'false' }],
    ['no expected_date', { ...testNightly(), expected_date: undefined }],
    ['no resident_id', { ...testNightly(), resident_id: 3 }],
    ['a night without a date', testNightly({ nights: [{ ...testNight(), date: undefined } as never] })],
    ['a night with null thresholds', testNightly({ nights: [{ ...testNight(), thresholds: null } as never] })],
    ['a null night', testNightly({ nights: [null as never] })],
  ])('throws ApiError(200, invalid_response) for %s', async (_label, body) => {
    fetchMock.mockResolvedValue(jsonResponse(body))
    expect(await apiErrorOf(getNightly('res-0003'))).toMatchObject({ status: 200, code: 'invalid_response' })
  })

  it.each([
    [403, 'forbidden'],
    [400, 'invalid_resident_id'],
  ])('passes through the API error %i %s', async (status, code) => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code, message: 'Synthetic message' } }, status))
    expect(await apiErrorOf(getNightly('res-0003'))).toMatchObject({ status, code })
  })

  it('a 500 is an ApiError with status 500', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code: 'internal_error', message: 'Synthetic' } }, 500))
    expect(await apiErrorOf(getNightly('res-0003'))).toMatchObject({ status: 500 })
  })

  it('unauthenticated without calling fetch', async () => {
    auth.getApiToken.mockResolvedValue(null)
    expect(await apiErrorOf(getNightly('res-0003'))).toMatchObject({ status: 401, code: 'unauthenticated' })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
