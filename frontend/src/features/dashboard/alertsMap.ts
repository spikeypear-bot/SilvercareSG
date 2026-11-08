import type { AlertSeverity, Home, Resident, ResidentAlert } from '../../types/dashboard'

/** One resident of a home and their open-list alerts, in the inbox's order. */
export interface HomeResident {
  resident_id: string
  resident: Resident | undefined
  alerts: ResidentAlert[]
}

/** One map pin: a home with open-list alerts and its worst severity. */
export interface HomePin {
  home: Home
  severity: AlertSeverity
  residents: HomeResident[]
}

/**
 * Groups open-list alerts (already sorted for the inbox) by home for the alerts map
 * (docs/features/alerts-map.md, criteria 15-17). `missing` holds one line per resident
 * with no entry in `homes` and per unassigned device, so no alert drops off the map.
 */
export function groupAlertsByHome(
  open: readonly ResidentAlert[],
  residentsById: Map<string, Resident>,
  homes: readonly Home[],
): { pins: HomePin[]; missing: string[] } {
  const homesById = new Map(homes.map((h) => [h.home_id, h]))
  const pinsById = new Map<string, HomePin>()
  const missing = new Map<string, string>()

  for (const alert of open) {
    if (alert.resident_id === 'unassigned') {
      missing.set(`device:${alert.device_id}`, `Unassigned device ${alert.device_id}: no home`)
      continue
    }
    const resident = residentsById.get(alert.resident_id)
    const home = resident && homesById.get(resident.home_id)
    if (!home) {
      const name = resident?.name ?? 'Unknown resident'
      const homeId = resident?.home_id || 'no home ID'
      missing.set(`resident:${alert.resident_id}`, `${name} (${homeId}): no location on file`)
      continue
    }
    let pin = pinsById.get(home.home_id)
    if (!pin) {
      pin = { home, severity: 'warning', residents: [] }
      pinsById.set(home.home_id, pin)
    }
    if (alert.severity === 'critical') pin.severity = 'critical'
    let entry = pin.residents.find((r) => r.resident_id === alert.resident_id)
    if (!entry) {
      entry = { resident_id: alert.resident_id, resident, alerts: [] }
      pin.residents.push(entry)
    }
    entry.alerts.push(alert)
  }

  // Pins in the server's order (by home_id).
  const pins = homes.flatMap((h) => pinsById.get(h.home_id) ?? [])
  return { pins, missing: [...missing.values()] }
}
