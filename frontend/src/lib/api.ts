/**
 * Typed client for the SilverCare HTTP API (infra/api.tf): the only module that calls
 * `fetch`.
 *
 * - Base URL from VITE_API_URL (public, set by the deploy workflow from Terraform).
 * - Sends the Cognito **ID token** from `getApiToken()` as `Authorization: Bearer`, and
 *   nothing else about the user: the server takes the caller from the token.
 * - Error responses (`{"error": {"code", "message"}}`) become an `ApiError` with the
 *   status and code. The token, URLs, and response bodies are never logged.
 */
import type { ApiDevice, DevicesResponse } from '../types/devices'
import { getApiToken } from './auth'

const ERROR_CODE_RE = /^[a-z][a-z0-9_]{0,63}$/

export class ApiError extends Error {
  /** HTTP status; 0 when there was no response (not configured, network error). */
  readonly status: number
  /** The API's error code, or one of not_configured, unauthenticated, network_error, invalid_response, http_<status>. */
  readonly code: string

  constructor(status: number, code: string) {
    super(`API request failed (${code})`)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

function baseUrl(): string {
  return (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')
}

async function errorCode(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && 'error' in body) {
      const err = (body as { error: unknown }).error
      if (typeof err === 'object' && err !== null && 'code' in err) {
        const code = (err as { code: unknown }).code
        if (typeof code === 'string' && ERROR_CODE_RE.test(code)) return code
      }
    }
  } catch {
    // Not JSON (e.g. an API Gateway error page): fall through.
  }
  return `http_${response.status}`
}

/** GET `path` (e.g. "/devices") with the caller's ID token; resolves to the parsed JSON. */
export async function apiGet(path: string): Promise<unknown> {
  const base = baseUrl()
  if (!base) throw new ApiError(0, 'not_configured')
  const token = await getApiToken()
  if (!token) throw new ApiError(401, 'unauthenticated')

  let response: Response
  try {
    response = await fetch(`${base}${path}`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
      cache: 'no-store',
      credentials: 'omit',
    })
  } catch {
    throw new ApiError(0, 'network_error')
  }
  if (!response.ok) throw new ApiError(response.status, await errorCode(response))
  try {
    return (await response.json()) as unknown
  } catch {
    throw new ApiError(response.status, 'invalid_response')
  }
}

function isDevicesResponse(value: unknown): value is DevicesResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    Array.isArray((value as { devices?: unknown }).devices)
  )
}

/** `GET /devices` (staff only): every device, sorted by device_id. */
export async function getDevices(): Promise<ApiDevice[]> {
  const body = await apiGet('/devices')
  if (!isDevicesResponse(body)) throw new ApiError(200, 'invalid_response')
  return body.devices
}
