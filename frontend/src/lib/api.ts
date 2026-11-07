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
import type { ApiDashboardResponse } from '../types/dashboard'
import type {
  ApiMyResidentsResponse,
  ApiResidentDetail,
  ApiThresholdChange,
  ApiThresholdsSaveResponse,
  VitalsThresholds,
} from '../types/resident'
import type { ApiDevice, DevicesResponse } from '../types/devices'
import type { NightlyResponse } from '../types/nightly'
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

async function apiRequest(method: 'GET' | 'POST' | 'PUT', path: string, body?: unknown): Promise<unknown> {
  const base = baseUrl()
  if (!base) throw new ApiError(0, 'not_configured')
  const token = await getApiToken()
  if (!token) throw new ApiError(401, 'unauthenticated')

  const headers: Record<string, string> = { Authorization: `Bearer ${token}`, Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(`${base}${path}`, {
      method,
      headers,
      ...(body !== undefined && { body: JSON.stringify(body) }),
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

/** GET `path` (e.g. "/devices") with the caller's ID token; resolves to the parsed JSON. */
export async function apiGet(path: string): Promise<unknown> {
  return apiRequest('GET', path)
}

/** POST a JSON `body` to `path` with the caller's ID token; resolves to the parsed JSON. */
export async function apiPost(path: string, body: unknown): Promise<unknown> {
  return apiRequest('POST', path, body)
}

/** PUT a JSON `body` to `path` with the caller's ID token; resolves to the parsed JSON. */
export async function apiPut(path: string, body: unknown): Promise<unknown> {
  return apiRequest('PUT', path, body)
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

function isDashboardResponse(value: unknown): value is ApiDashboardResponse {
  if (typeof value !== 'object' || value === null) return false
  const body = value as { residents?: unknown; alerts?: unknown }
  return Array.isArray(body.residents) && Array.isArray(body.alerts)
}

/**
 * `GET /dashboard` (staff only): every resident with their status and device state,
 * sorted by resident_id, and the open list (open and acknowledged alerts), newest first.
 */
export async function getDashboard(): Promise<ApiDashboardResponse> {
  const body = await apiGet('/dashboard')
  if (!isDashboardResponse(body)) throw new ApiError(200, 'invalid_response')
  return body
}

/** `202` body of the alert actions: the request is queued, not yet applied. */
export interface AlertActionResponse {
  alert_id: string
  requested_status: 'acknowledged' | 'cancelled' | 'resolved'
}

/** A 202 body for this alert whose `requested_status` is the one this action asked for. */
function isAlertActionResponse(
  value: unknown,
  alertId: string,
  expected: AlertActionResponse['requested_status'],
): value is AlertActionResponse {
  if (typeof value !== 'object' || value === null) return false
  const body = value as { alert_id?: unknown; requested_status?: unknown }
  return body.alert_id === alertId && body.requested_status === expected
}

function alertPath(residentId: string, alertId: string, action: 'acknowledge' | 'cancel' | 'resolve'): string {
  return `/residents/${encodeURIComponent(residentId)}/alerts/${encodeURIComponent(alertId)}/${action}`
}

/**
 * `POST .../acknowledge`: a nurse (health alert), ops or a linked caregiver (device
 * problem). Sends only the alert's `event_ts` and the nurse's note (null for none): the
 * server takes the actor from the token.
 */
export async function acknowledgeAlertApi(
  alert: { resident_id: string; alert_id: string; event_ts: string },
  note: string | null,
): Promise<AlertActionResponse> {
  const body = await apiPost(alertPath(alert.resident_id, alert.alert_id, 'acknowledge'), {
    event_ts: alert.event_ts,
    note,
  })
  if (!isAlertActionResponse(body, alert.alert_id, 'acknowledged')) throw new ApiError(202, 'invalid_response')
  return body
}

/** `POST .../cancel`: a linked caregiver, health alerts only. Sends only `event_ts`. */
export async function cancelAlertApi(alert: {
  resident_id: string
  alert_id: string
  event_ts: string
}): Promise<AlertActionResponse> {
  const body = await apiPost(alertPath(alert.resident_id, alert.alert_id, 'cancel'), {
    event_ts: alert.event_ts,
  })
  if (!isAlertActionResponse(body, alert.alert_id, 'cancelled')) throw new ApiError(202, 'invalid_response')
  return body
}

/**
 * `POST .../resolve`: a nurse or doctor, health alerts only (device problems are resolved
 * by the watchdog). Sends only the alert's `event_ts` and the required closing note
 * (already normalised by `checkResolveNote`): the server takes the actor from the token.
 */
export async function resolveAlertApi(
  alert: { resident_id: string; alert_id: string; event_ts: string },
  note: string,
): Promise<AlertActionResponse> {
  const body = await apiPost(alertPath(alert.resident_id, alert.alert_id, 'resolve'), {
    event_ts: alert.event_ts,
    note,
  })
  if (!isAlertActionResponse(body, alert.alert_id, 'resolved')) throw new ApiError(202, 'invalid_response')
  return body
}

/**
 * `GET /residents/{resident_id}` (staff any resident; a caregiver only when linked, else
 * 404): devices with their latest 5 entries per message type, every stored alert, and
 * the resolved thresholds.
 */
export async function getResident(residentId: string): Promise<ApiResidentDetail> {
  const body = await apiGet(`/residents/${encodeURIComponent(residentId)}`)
  const ok =
    typeof body === 'object' &&
    body !== null &&
    typeof (body as { resident?: unknown }).resident === 'object' &&
    (body as { resident?: unknown }).resident !== null &&
    Array.isArray((body as { alerts?: unknown }).alerts) &&
    typeof (body as { thresholds?: unknown }).thresholds === 'object'
  if (!ok) throw new ApiError(200, 'invalid_response')
  return body as ApiResidentDetail
}

/** `GET /me/residents` (caregiver only): the linked residents and all their alerts. */
export async function getMyResidents(): Promise<ApiMyResidentsResponse> {
  const body = await apiGet('/me/residents')
  if (!isDashboardResponse(body)) throw new ApiError(200, 'invalid_response')
  return body as unknown as ApiMyResidentsResponse
}

function thresholdsPath(residentId: string): string {
  return `/residents/${encodeURIComponent(residentId)}/thresholds`
}

/**
 * `PUT /residents/{resident_id}/thresholds` (doctor only, pattern 26). Sends exactly the
 * five limits, copied by name so nothing else (e.g. updated_by) can ride along; the server
 * takes the actor from the token and records the change. `409` if someone else saved
 * first, `400` if the server refuses a value.
 */
export async function putThresholds(
  residentId: string,
  values: VitalsThresholds,
): Promise<ApiThresholdsSaveResponse> {
  const body = await apiPut(thresholdsPath(residentId), {
    hr_bpm: { min: values.hr_bpm.min, max: values.hr_bpm.max },
    spo2_pct: { min: values.spo2_pct.min },
    skin_temp_c: { min: values.skin_temp_c.min, max: values.skin_temp_c.max },
  })
  const ok =
    typeof body === 'object' &&
    body !== null &&
    typeof (body as { changed?: unknown }).changed === 'boolean' &&
    typeof (body as { thresholds?: unknown }).thresholds === 'object' &&
    (body as { thresholds?: unknown }).thresholds !== null
  if (!ok) throw new ApiError(200, 'invalid_response')
  return body as ApiThresholdsSaveResponse
}

/** `GET /residents/{resident_id}/thresholds/history` (staff only, pattern 27): newest first. */
export async function getThresholdHistory(residentId: string): Promise<ApiThresholdChange[]> {
  const body = await apiGet(`${thresholdsPath(residentId)}/history`)
  if (typeof body !== 'object' || body === null || !Array.isArray((body as { history?: unknown }).history)) {
    throw new ApiError(200, 'invalid_response')
  }
  return (body as { history: ApiThresholdChange[] }).history
}

function isNightlyResponse(value: unknown): value is NightlyResponse {
  if (typeof value !== 'object' || value === null) return false
  const body = value as { resident_id?: unknown; expected_date?: unknown; not_ready?: unknown; nights?: unknown }
  return (
    typeof body.resident_id === 'string' &&
    typeof body.expected_date === 'string' &&
    typeof body.not_ready === 'boolean' &&
    Array.isArray(body.nights) &&
    body.nights.every(
      (night: unknown) =>
        typeof night === 'object' &&
        night !== null &&
        typeof (night as { date?: unknown }).date === 'string' &&
        typeof (night as { thresholds?: unknown }).thresholds === 'object' &&
        (night as { thresholds?: unknown }).thresholds !== null,
    )
  )
}

/**
 * `GET /residents/{resident_id}/nightly` (doctor only): the resident's stored nights
 * (up to 30), newest first, and whether last night's summary is still missing.
 */
export async function getNightly(residentId: string): Promise<NightlyResponse> {
  const body = await apiGet(`/residents/${encodeURIComponent(residentId)}/nightly`)
  if (!isNightlyResponse(body)) throw new ApiError(200, 'invalid_response')
  return body
}
