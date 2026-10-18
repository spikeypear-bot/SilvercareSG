import {
  BatteryCharging,
  BatteryFull,
  BatteryLow,
  BatteryMedium,
  BedDouble,
  Watch,
  Wifi,
  WifiOff,
} from 'lucide-react'
import type { BedroomSensorState, WearableState } from '../../types/dashboard'
import { LOW_BATTERY_DISPLAY_PCT } from './labels'

export function BatteryLevel({ wearable }: { wearable: WearableState }) {
  const { battery_pct: pct, charging } = wearable
  const Icon = charging
    ? BatteryCharging
    : pct < LOW_BATTERY_DISPLAY_PCT
      ? BatteryLow
      : pct < 60
        ? BatteryMedium
        : BatteryFull
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon className="size-5 shrink-0 text-slate-600" aria-hidden="true" />
      <span>{pct}%</span>
    </span>
  )
}

/** "Worn", "Not worn", "Charging", "Offline": icon + text. */
export function WearableStatusText({ wearable }: { wearable: WearableState }) {
  const parts: string[] = []
  if (!wearable.online) parts.push('Offline')
  parts.push(wearable.worn ? 'Worn' : 'Not worn')
  if (wearable.charging) parts.push('Charging')
  return (
    <span className="inline-flex items-center gap-1.5">
      {wearable.online ? (
        <Watch className="size-5 shrink-0 text-slate-600" aria-hidden="true" />
      ) : (
        <WifiOff className="size-5 shrink-0 text-rose-700" aria-hidden="true" />
      )}
      <span>{parts.join(' · ')}</span>
    </span>
  )
}

export function BedroomStatusText({ bedroom }: { bedroom: BedroomSensorState }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <BedDouble className="size-5 shrink-0 text-slate-600" aria-hidden="true" />
      {bedroom.online ? (
        <span className="inline-flex items-center gap-1">
          <Wifi className="size-4 text-emerald-700" aria-hidden="true" />
          Online
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 font-medium text-rose-800">
          <WifiOff className="size-4" aria-hidden="true" />
          Offline
        </span>
      )}
    </span>
  )
}
