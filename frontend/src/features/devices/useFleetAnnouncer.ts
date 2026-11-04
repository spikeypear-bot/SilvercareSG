import { useState } from 'react'
import type { FleetDevice } from '../../types/devices'
import { useAnnouncer } from '../dashboard/useAnnouncer'
import { DEVICE_TYPE_LABELS } from './fleet'

function describe(device: FleetDevice): string {
  return `${DEVICE_TYPE_LABELS[device.device_type]} ${device.device_id}`
}

function changeMessage(wentOffline: FleetDevice[], backOnline: FleetDevice[]): string {
  const parts: string[] = []
  if (wentOffline.length === 1) parts.push(`${describe(wentOffline[0])} went offline.`)
  if (wentOffline.length > 1) parts.push(`${wentOffline.length} devices went offline.`)
  if (backOnline.length === 1) parts.push(`${describe(backOnline[0])} is back online.`)
  if (backOnline.length > 1) parts.push(`${backOnline.length} devices are back online.`)
  return parts.join(' ')
}

/**
 * The devices page's one screen-reader announcer. Announces devices that go offline or
 * come back after the first load (when polling arrives), plus anything passed to
 * `announce` (the filter result). The table is not a live region.
 */
export function useFleetAnnouncer(devices: FleetDevice[]): {
  message: string
  announce: (message: string) => void
} {
  const { message, announce } = useAnnouncer()
  const [previous, setPrevious] = useState(devices)

  // Compare with the previous render's devices (React's "adjust state on prop change").
  if (devices !== previous) {
    const wasOnline = new Map(previous.map((d) => [d.device_id, d.online]))
    const wentOffline = devices.filter((d) => !d.online && wasOnline.get(d.device_id) === true)
    const backOnline = devices.filter((d) => d.online && wasOnline.get(d.device_id) === false)
    setPrevious(devices)
    const text = changeMessage(wentOffline, backOnline)
    if (text) announce(text)
  }

  return { message, announce }
}
