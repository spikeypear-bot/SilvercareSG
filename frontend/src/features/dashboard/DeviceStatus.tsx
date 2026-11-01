import {
  BatteryCharging,
  BatteryFull,
  BatteryLow,
  BatteryMedium,
  BedDouble,
  Plug,
  Watch,
  Wifi,
  WifiOff,
} from 'lucide-react'
import type { BedroomSensorState, WearableState } from '../../types/dashboard'
import { LOW_BATTERY_DISPLAY_PCT } from './labels'

const BATTERY_ICON_CLASS = 'size-5 shrink-0 text-slate-600'

function BatteryIcon({ pct, charging }: { pct: number; charging: boolean }) {
  if (charging) return <BatteryCharging className={BATTERY_ICON_CLASS} aria-hidden="true" />
  if (pct < LOW_BATTERY_DISPLAY_PCT) return <BatteryLow className={BATTERY_ICON_CLASS} aria-hidden="true" />
  if (pct < 60) return <BatteryMedium className={BATTERY_ICON_CLASS} aria-hidden="true" />
  return <BatteryFull className={BATTERY_ICON_CLASS} aria-hidden="true" />
}

export function BatteryLevel({ wearable }: { wearable: Pick<WearableState, 'battery_pct' | 'charging'> }) {
  const { battery_pct: pct, charging } = wearable
  return (
    <span className="inline-flex items-center gap-1.5">
      <BatteryIcon pct={pct} charging={charging} />
      <span>{pct}%</span>
    </span>
  )
}

interface DeviceBatteryProps {
  battery_pct: number | null
  charging: boolean
  /** A reported bedroom sensor without a battery. Otherwise a null level is "Unknown". */
  mainsPowered: boolean
}

/**
 * Battery for any device, icon + text: "64%", "12% · Low" (below the display filter),
 * "58% · Charging", "Mains powered", or "Unknown" (null and not mains-powered, e.g. a
 * device that has never reported). The text is one inline span, so its spaces survive.
 */
export function DeviceBattery({ battery_pct, charging, mainsPowered }: DeviceBatteryProps) {
  if (battery_pct === null) {
    return (
      <span className="inline-flex items-center gap-1.5">
        {mainsPowered ? (
          <>
            <Plug className="size-5 shrink-0 text-slate-600" aria-hidden="true" />
            <span>Mains powered</span>
          </>
        ) : (
          <span className="text-slate-600">Unknown</span>
        )}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <BatteryIcon pct={battery_pct} charging={charging} />
      <span>
        {battery_pct}%
        {battery_pct < LOW_BATTERY_DISPLAY_PCT && (
          <>
            {' '}
            <span className="font-semibold text-rose-800">· Low</span>
          </>
        )}
        {charging && ' · Charging'}
      </span>
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
      <OnlineStatus online={bedroom.online} />
    </span>
  )
}

/** "Online" / "Offline": icon + text. */
export function OnlineStatus({ online }: { online: boolean }) {
  return online ? (
    <span className="inline-flex items-center gap-1">
      <Wifi className="size-4 text-emerald-700" aria-hidden="true" />
      Online
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 font-medium text-rose-800">
      <WifiOff className="size-4" aria-hidden="true" />
      Offline
    </span>
  )
}
