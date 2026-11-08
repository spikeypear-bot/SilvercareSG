import { useEffect, useMemo, useRef, useState, type Ref } from 'react'
import { Link } from 'react-router-dom'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { Notice } from '../../components/Notice'
import { RelativeTime } from '../../components/RelativeTime'
import { useNow } from '../../lib/useNow'
import type { Home, Resident, ResidentAlert } from '../../types/dashboard'
import { LifecycleLabel } from './AlertRow'
import { groupAlertsByHome, type HomePin } from './alertsMap'
import { describeAlert } from './useAlertAnnouncer'

// OneMap basemap (docs/features/alerts-map.md, criterion 14). Bounds and zooms from OneMap's TileJSON.
const TILE_URL = 'https://www.onemap.gov.sg/maps/tiles/Default/{z}/{x}/{y}.png'
const BOUNDS = L.latLngBounds([1.16, 103.502], [1.56073, 104.11475])
const MIN_ZOOM = 11
const MAX_ZOOM = 19
// Classes, not style attributes: the CSP's style-src is 'self'.
const ATTRIBUTION =
  '<span class="inline-flex flex-wrap items-center gap-1 text-base">' +
  '<img src="https://www.onemap.gov.sg/web-assets/images/logo/om_logo.png" alt="" class="inline size-5" />' +
  '<span><a href="https://www.onemap.gov.sg/" target="_blank" rel="noopener noreferrer">OneMap</a>' +
  ' © contributors | Singapore Land Authority</span></span>'

interface AlertsMapProps {
  /** Open-list alerts, sorted as the inbox lists them. */
  alerts: ResidentAlert[]
  residentsById: Map<string, Resident>
  /** Absent when the response had none. */
  homes: Home[] | undefined
  homesError: boolean
}

const NO_HOMES: Home[] = []

/** The nurse's Map view: one pin per home with open-list alerts, details as real text below. */
export function AlertsMap({ alerts, residentsById, homes, homesError }: AlertsMapProps) {
  const { pins, missing } = useMemo(
    () => groupAlertsByHome(alerts, residentsById, homes ?? NO_HOMES),
    [alerts, residentsById, homes],
  )
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = pins.find((p) => p.home.home_id === selectedId)
  const detailsRef = useRef<HTMLHeadingElement>(null)
  const focusDetails = useRef(false)

  useEffect(() => {
    if (selected && focusDetails.current) {
      focusDetails.current = false
      detailsRef.current?.focus()
    }
  }, [selected])

  return (
    <div className="flex flex-col gap-4">
      {homesError && (
        <Notice tone="warning">Couldn't load home locations. The alert list still works.</Notice>
      )}
      <LeafletMap
        pins={pins}
        onSelect={(homeId) => {
          focusDetails.current = true
          setSelectedId(homeId)
        }}
      />
      <p className="text-base text-slate-600">
        Select a pin (Tab, then Enter) to see the home's residents and alerts.
      </p>
      {selected && <HomeDetails pin={selected} residentsById={residentsById} headingRef={detailsRef} />}
      {missing.length > 0 && (
        <ul className="flex flex-col gap-1 text-base text-slate-800">
          {missing.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

function LeafletMap({ pins, onSelect }: { pins: HomePin[]; onSelect: (homeId: string) => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  // Markers kept by home_id across polls, so a focused pin keeps focus when nothing changed.
  const markersRef = useRef(new Map<string, { marker: L.Marker; signature: string }>())
  const fittedRef = useRef(false)
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  })

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const map = L.map(container, {
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      maxBounds: BOUNDS,
      maxBoundsViscosity: 1,
    })
    map.attributionControl.setPrefix(false)
    L.tileLayer(TILE_URL, { minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM, attribution: ATTRIBUTION }).addTo(map)
    // Leaflet's zoom buttons are 30 px; tap targets must be at least 44 px.
    map.zoomControl
      .getContainer()
      ?.querySelectorAll('a')
      .forEach((a) => a.classList.add('size-11!', 'leading-11!', 'text-2xl!'))
    map.fitBounds(BOUNDS)
    mapRef.current = map
    const markers = markersRef.current
    return () => {
      map.remove()
      markers.clear()
      mapRef.current = null
      fittedRef.current = false
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const markers = markersRef.current
    const ids = new Set(pins.map((p) => p.home.home_id))
    for (const [id, { marker }] of markers) {
      if (!ids.has(id)) {
        marker.remove()
        markers.delete(id)
      }
    }
    for (const pin of pins) {
      const { home_id, lat, lng } = pin.home
      const signature = `${pin.severity}, ${pinLabel(pin)}`
      const existing = markers.get(home_id)
      if (existing) {
        existing.marker.setLatLng([lat, lng])
        if (existing.signature !== signature) {
          existing.marker.setIcon(pinIcon(pin))
          existing.signature = signature
          bindKeyboard(existing.marker, home_id)
        }
        continue
      }
      const marker = L.marker([lat, lng], { icon: pinIcon(pin), keyboard: true })
        .on('click', () => onSelectRef.current(home_id))
        .addTo(map)
      bindKeyboard(marker, home_id)
      markers.set(home_id, { marker, signature })
    }
    // Fit only once, so the 30-second refresh keeps the user's pan and zoom.
    // Cap the automatic zoom at 14 so tile requests to OneMap reveal a neighbourhood, not a street (criterion 14).
    if (!fittedRef.current && pins.length > 0) {
      fittedRef.current = true
      if (pins.length === 1) map.setView([pins[0].home.lat, pins[0].home.lng], 14)
      else map.fitBounds(L.latLngBounds(pins.map((p) => [p.home.lat, p.home.lng])), { padding: [48, 48], maxZoom: 14 })
    }

    function bindKeyboard(marker: L.Marker, homeId: string) {
      // Leaflet 1.9 makes markers focusable (role="button") but doesn't open them on Enter.
      marker.getElement()?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelectRef.current(homeId)
        }
      })
    }
  }, [pins])

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label="Map of homes with open alerts"
      className="isolate h-80 w-full rounded-xl border border-slate-200 sm:h-96"
    />
  )
}

/** "Critical, home-0003, Test address 3 (synthetic), 2 alerts": the pin's accessible name. */
function pinLabel(pin: HomePin): string {
  const count = pin.residents.reduce((n, r) => n + r.alerts.length, 0)
  return `${pin.home.home_id}, ${pin.home.address}, ${count} ${count === 1 ? 'alert' : 'alerts'}`
}

/** Severity as an icon and text, never colour alone. Built with DOM nodes, so the address is never parsed as HTML. */
function pinIcon(pin: HomePin): L.DivIcon {
  const critical = pin.severity === 'critical'
  const html = document.createElement('span')
  html.className = 'flex size-full items-center justify-center gap-1.5'
  const icon = document.createElement('span')
  icon.setAttribute('aria-hidden', 'true')
  icon.className = critical
    ? 'flex size-6 items-center justify-center rounded-full bg-white text-base font-bold text-rose-800'
    : 'text-base'
  icon.textContent = critical ? '!' : '▲'
  const text = document.createElement('span')
  text.textContent = critical ? 'Critical' : 'Warning'
  const extra = document.createElement('span')
  extra.className = 'sr-only'
  extra.textContent = `, ${pinLabel(pin)}`
  html.append(icon, text, extra)
  return L.divIcon({
    html,
    iconSize: [116, 44],
    className: critical
      ? 'rounded-full border-2 border-white bg-rose-700 text-base font-semibold text-white shadow-md focus-visible:ring-4 focus-visible:ring-teal-600/60'
      : 'rounded-full border-2 border-amber-700 bg-amber-100 text-base font-semibold text-amber-950 shadow-md focus-visible:ring-4 focus-visible:ring-teal-600/60',
  })
}

// ---------------------------------------------------------------------------

function HomeDetails({
  pin,
  residentsById,
  headingRef,
}: {
  pin: HomePin
  residentsById: Map<string, Resident>
  headingRef: Ref<HTMLHeadingElement>
}) {
  const now = useNow()
  return (
    <section aria-labelledby={`home-${pin.home.home_id}`} className="flex flex-col gap-3 rounded-xl border border-slate-200 p-4">
      <h3
        id={`home-${pin.home.home_id}`}
        ref={headingRef}
        tabIndex={-1}
        className="text-base font-semibold text-slate-900 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40"
      >
        {pin.home.home_id}
      </h3>
      <ul className="flex flex-col divide-y divide-slate-200">
        {pin.residents.map(({ resident_id, resident, alerts }) => (
          <li key={resident_id} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
            <p className="text-base font-semibold text-slate-900">{resident?.name ?? 'Unknown resident'}</p>
            <p className="text-base text-slate-700">{pin.home.address}</p>
            <ul className="flex flex-col gap-2">
              {alerts.map((alert) => (
                <li key={alert.alert_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-base text-slate-800">
                  <span className="font-medium">{describeAlert(alert, residentsById)}</span>
                  <LifecycleLabel alert={alert} />
                  <span className="text-sm text-slate-600">
                    <RelativeTime iso={alert.event_ts} now={now} />
                  </span>
                </li>
              ))}
            </ul>
            <Link
              to={`/residents/${encodeURIComponent(resident_id)}`}
              className="inline-flex min-h-11 items-center self-start rounded font-medium text-teal-800 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40"
            >
              View resident
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
