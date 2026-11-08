import { useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { BatteryLow, BedDouble, CircleCheck, Cpu, House, Watch, WifiOff } from 'lucide-react'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { EmptyState } from '../../components/EmptyState'
import { RelativeTime } from '../../components/RelativeTime'
import { Skeleton } from '../../components/Skeleton'
import { useNow } from '../../lib/useNow'
import type { FleetDevice } from '../../types/devices'
import { AlertAnnouncer } from '../dashboard/AlertAnnouncer'
import { useAuth } from '../auth/useAuth'
import { DashboardError, LoadingStatus, RefreshFailedNotice } from '../dashboard/DashboardStates'
import { DeviceBattery, OnlineStatus } from '../dashboard/DeviceStatus'
import { LOW_BATTERY_DISPLAY_PCT } from '../dashboard/labels'
import { MainLayout } from '../dashboard/MainLayout'
import { StaffNav } from '../dashboard/StaffNav'
import { StaffOnly } from '../dashboard/StaffOnly'
import { StatTile, StatTilesSkeleton } from '../dashboard/StatTile'
import { useDeviceFleet } from '../dashboard/useDashboardData'
import { ALERT_LIMITS_STATUS_LABELS, formatDeviceLimits } from './alertLimits'
import { DEVICE_TYPE_LABELS, filterDevices, fleetStats, sortForTriage, type DeviceFilter } from './fleet'
import { useFleetAnnouncer } from './useFleetAnnouncer'

/**
 * Staff and ops area tab: every wearable and bedroom sensor. Read-only, no device
 * actions; polls every 30 s. ops get no resident names or homes (the API sends only the
 * resident ID) and no link to the resident page, which answers ops with 403.
 */
export function DevicesPage() {
  const { view } = useAuth()
  if (view === 'ops') return <FleetPage forOps />
  return <StaffOnly>{() => <FleetPage forOps={false} />}</StaffOnly>
}

function FleetPage({ forOps }: { forOps: boolean }) {
  const fleet = useDeviceFleet()
  return (
    <MainLayout title="Devices" showGreeting={false} before={<StaffNav label={forOps ? 'Ops area' : 'Staff area'} />}>
      {fleet.refreshFailed && fleet.data && <RefreshFailedNotice polling />}
      {fleet.error ? (
        <DashboardError title="We couldn't load the devices." onRetry={fleet.retry} />
      ) : fleet.loading || !fleet.data ? (
        <DevicesSkeleton />
      ) : (
        <DevicesContent devices={fleet.data} forOps={forOps} />
      )}
    </MainLayout>
  )
}

const FILTERS: ReadonlyArray<{
  id: DeviceFilter
  label: string
  /** Used in "Showing 6 of 40 devices: offline." */
  spoken: string
  empty: string
  detail?: string
  icon: ReactNode
}> = [
  {
    id: 'all',
    label: 'All devices',
    spoken: 'all',
    empty: 'No devices yet.',
    icon: <Cpu className="size-6" aria-hidden="true" />,
  },
  {
    id: 'offline',
    label: 'Offline',
    spoken: 'offline',
    empty: 'All devices are online.',
    detail: 'No status for 15 min',
    icon: <WifiOff className="size-6" aria-hidden="true" />,
  },
  {
    id: 'lowBattery',
    label: 'Low battery',
    spoken: 'low battery',
    empty: `No devices are below ${LOW_BATTERY_DISPLAY_PCT}%.`,
    detail: `Below ${LOW_BATTERY_DISPLAY_PCT}%`,
    icon: <BatteryLow className="size-6" aria-hidden="true" />,
  },
  {
    id: 'normal',
    label: 'Reporting normally',
    spoken: 'reporting normally',
    empty: 'No devices are reporting normally.',
    detail: 'Online, battery OK',
    icon: <CircleCheck className="size-6" aria-hidden="true" />,
  },
]

function DevicesContent({ devices, forOps }: { devices: FleetDevice[]; forOps: boolean }) {
  const [filter, setFilter] = useState<DeviceFilter>('all')
  const { message, announce } = useFleetAnnouncer(devices)
  const stats = fleetStats(devices)
  const counts: Record<DeviceFilter, number> = {
    all: stats.total,
    offline: stats.offline,
    lowBattery: stats.lowBattery,
    normal: stats.normal,
  }
  const sorted = useMemo(() => sortForTriage(devices), [devices])
  const visible = filterDevices(sorted, filter)
  const current = FILTERS.find((f) => f.id === filter) ?? FILTERS[0]

  function choose(next: DeviceFilter) {
    setFilter(next)
    const spec = FILTERS.find((f) => f.id === next) ?? FILTERS[0]
    announce(`Showing ${counts[next]} of ${stats.total} devices: ${spec.spoken}.`)
  }

  return (
    <>
      <AlertAnnouncer message={message} />
      <section aria-labelledby="device-stats-heading">
        <h2 id="device-stats-heading" className="sr-only">
          Summary
        </h2>
        <div
          role="group"
          aria-label="Choose which devices the list shows"
          className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
        >
          {FILTERS.map((f) => (
            <StatTile
              key={f.id}
              label={f.label}
              value={counts[f.id]}
              detail={f.detail}
              icon={f.icon}
              selected={filter === f.id}
              onSelect={() => choose(f.id)}
            />
          ))}
        </div>
      </section>

      {devices.length === 0 ? (
        <EmptyState title="No devices yet" description="Devices appear here once they are registered." />
      ) : (
        <DeviceTable
          devices={visible}
          title={filter === 'all' ? 'All devices' : current.label}
          emptyText={current.empty}
          onShowAll={filter === 'all' ? undefined : () => choose('all')}
          forOps={forOps}
        />
      )}
    </>
  )
}

function DeviceTable({
  devices,
  title,
  emptyText,
  onShowAll,
  forOps,
}: {
  devices: FleetDevice[]
  title: string
  emptyText: string
  onShowAll?: () => void
  forOps: boolean
}) {
  const headingId = useId()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const now = useNow()

  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 id={headingId} ref={headingRef} tabIndex={-1} className="text-lg font-semibold text-slate-900">
            {title} <span className="font-normal text-slate-600">({devices.length})</span>
          </h2>
          <p className="text-sm text-slate-600">
            Problems first: offline (longest silent first), then low battery (lowest first).
          </p>
        </div>

        {devices.length === 0 ? (
          <div className="flex flex-col items-start gap-3">
            <p className="text-base text-slate-600">{emptyText}</p>
            {onShowAll && (
              <Button
                variant="secondary"
                onClick={() => {
                  onShowAll()
                  // The button goes away with the empty list; keep focus on the list.
                  headingRef.current?.focus()
                }}
              >
                Show all devices
              </Button>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-max border-collapse text-left text-base">
              <caption className="sr-only">
                {title}, {devices.length} devices
              </caption>
              <thead>
                <tr className="border-b border-slate-200 text-sm text-slate-600">
                  {['Device', 'Type', 'Assigned to', 'Status', 'Battery', 'Last seen', 'Firmware', 'Alert limits'].map((h) => (
                    <th key={h} scope="col" className="py-2 pr-4 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => (
                  <DeviceRow key={device.device_id} device={device} now={now} forOps={forOps} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </Card>
  )
}

function DeviceRow({ device, now, forOps }: { device: FleetDevice; now: Date; forOps: boolean }) {
  const TypeIcon = device.device_type === 'wearable' ? Watch : BedDouble
  return (
    <tr className="border-b border-slate-100 align-top last:border-0">
      <th scope="row" className="py-3 pr-4 font-semibold text-slate-900">
        {device.device_id}
      </th>
      <td className="py-3 pr-4 text-slate-800">
        <span className="inline-flex items-center gap-1.5">
          <TypeIcon className="size-5 shrink-0 text-slate-600" aria-hidden="true" />
          {DEVICE_TYPE_LABELS[device.device_type]}
        </span>
      </td>
      <td className="py-3 pr-4">
        {device.assigned_to && forOps ? (
          <span className="text-slate-800">Resident {device.assigned_to.resident_id}</span>
        ) : device.assigned_to ? (
          <span className="flex flex-col">
            <Link
              to={`/residents/${encodeURIComponent(device.assigned_to.resident_id)}`}
              className="rounded font-medium text-teal-800 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40"
            >
              {device.assigned_to.name}
            </Link>
            <span className="inline-flex items-center gap-1 text-sm text-slate-600">
              <House className="size-4" aria-hidden="true" />
              <span className="sr-only">Home</span>
              {device.assigned_to.home_id}
            </span>
          </span>
        ) : (
          <span className="text-slate-600">Not assigned</span>
        )}
      </td>
      <td className="py-3 pr-4 text-slate-900">
        <OnlineStatus online={device.online} />
      </td>
      <td className="py-3 pr-4 text-slate-900">
        <DeviceBattery
          battery_pct={device.battery_pct}
          charging={device.charging}
          mainsPowered={device.device_type === 'bedroom' && device.last_seen !== null}
        />
      </td>
      <td className="py-3 pr-4 text-slate-700">
        {device.last_seen ? <RelativeTime iso={device.last_seen} now={now} /> : 'Never'}
      </td>
      <td className="py-3 pr-4 text-slate-700">{device.fw_version ?? 'Unknown'}</td>
      <td className="py-3 pr-4">
        <AlertLimitsCell device={device} forOps={forOps} />
      </td>
    </tr>
  )
}

/** Criteria 16-17: limits in use and sync status; ops get the status text only. */
function AlertLimitsCell({ device, forOps }: { device: FleetDevice; forOps: boolean }) {
  if (device.device_type !== 'wearable') {
    return (
      <span className="text-slate-600">
        <span aria-hidden="true">—</span>
        <span className="sr-only">No alert limits</span>
      </span>
    )
  }
  const limits = device.alert_limits
  const status = <span className="text-slate-900">{ALERT_LIMITS_STATUS_LABELS[limits?.status ?? 'unknown']}</span>
  if (forOps) return status
  return (
    <span className="flex flex-col">
      {/* sr-only full stops so screen readers don't run the lines together */}
      <span className="text-slate-800">
        {limits?.reported ? formatDeviceLimits(limits.reported) : 'No limits reported'}
        <span className="sr-only">. </span>
      </span>
      {status}
      {limits?.status === 'pending' && limits.desired && (
        <span className="text-slate-700">
          <span className="sr-only">. </span>
          {`New: ${formatDeviceLimits(limits.desired)}`}
        </span>
      )}
    </span>
  )
}

function DevicesSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <LoadingStatus label="Loading devices" />
      <StatTilesSkeleton />
      <Card className="flex flex-col gap-3 p-6">
        <Skeleton className="h-6 w-40" />
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </Card>
    </div>
  )
}
