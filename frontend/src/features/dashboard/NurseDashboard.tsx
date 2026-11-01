import { useId, useMemo, useRef, useState, type ReactNode, type Ref } from 'react'
import { WifiOff } from 'lucide-react'
import { Card } from '../../components/Card'
import { RelativeTime } from '../../components/RelativeTime'
import { Skeleton } from '../../components/Skeleton'
import { useNow } from '../../lib/useNow'
import type { DashboardData, Resident, ResidentAlert } from '../../types/dashboard'
import { useAuth } from '../auth/useAuth'
import { AlertAnnouncer } from './AlertAnnouncer'
import { AlertRow } from './AlertRow'
import { BatteryLevel, WearableStatusText } from './DeviceStatus'
import { DashboardError, LoadingStatus } from './DashboardStates'
import { LOW_BATTERY_DISPLAY_PCT } from './labels'
import { MainLayout } from './MainLayout'
import { ResidentLine } from './ResidentLine'
import { ResidentsPanel } from './ResidentsPanel'
import { StaffNav } from './StaffNav'
import { StatTiles, StatTilesSkeleton, type StatId } from './StatTile'
import {
  dashboardStats,
  lowBatteryResidents,
  offlineDevices,
  sortAlertsForInbox,
  unresolvedAlerts,
} from './stats'
import { describeAlert, useAlertAnnouncer } from './useAlertAnnouncer'
import { useDashboardData } from './useDashboardData'
import { useEscalationWatcher } from './useEscalationWatcher'

/** Nurse view: alert response. Acknowledge and resolve, see what needs a visit. */
export function NurseDashboard() {
  const { data, loading, error, retry, acknowledgeAlert, resolveAlert } = useDashboardData('staff')

  return (
    <MainLayout title="Nurse dashboard" before={<StaffNav />}>
      {error ? (
        <DashboardError onRetry={retry} />
      ) : loading || !data ? (
        <NurseSkeleton />
      ) : (
        <NurseContent data={data} onAcknowledge={acknowledgeAlert} onResolve={resolveAlert} />
      )}
    </MainLayout>
  )
}

// ---------------------------------------------------------------------------

interface NurseContentProps {
  data: DashboardData
  onAcknowledge: (alertId: string, by: string) => void
  onResolve: (alertId: string, by: string) => void
}

function NurseContent({ data, onAcknowledge, onResolve }: NurseContentProps) {
  const { user } = useAuth()
  const staffName = user?.name || 'Staff member'
  const [panel, setPanel] = useState<StatId>('alerts')
  const residentsById = useMemo(
    () => new Map(data.residents.map((r) => [r.resident_id, r])),
    [data.residents],
  )
  const { message, announce } = useAlertAnnouncer(data.alerts, residentsById)
  useEscalationWatcher(data.alerts, (alert) =>
    announce(`Escalated to nurses: ${describeAlert(alert, residentsById)}.`),
  )

  return (
    <>
      <AlertAnnouncer message={message} />
      <StatTiles stats={dashboardStats(data)} selected={panel} onSelect={setPanel} />
      {panel === 'alerts' && (
        <AlertInbox
          alerts={data.alerts}
          residentsById={residentsById}
          announce={announce}
          onAcknowledge={(id) => onAcknowledge(id, staffName)}
          onResolve={(id) => onResolve(id, staffName)}
        />
      )}
      {panel === 'offline' && <OfflineDevicesPanel residents={data.residents} />}
      {panel === 'lowBattery' && <LowBatteryPanel residents={data.residents} />}
      {panel === 'residents' && <ResidentsPanel residents={data.residents} />}
    </>
  )
}

// ---------------------------------------------------------------------------

interface AlertInboxProps {
  alerts: ResidentAlert[]
  residentsById: Map<string, Resident>
  announce: (message: string) => void
  onAcknowledge: (alertId: string) => void
  onResolve: (alertId: string) => void
}

function AlertInbox({ alerts, residentsById, announce, onAcknowledge, onResolve }: AlertInboxProps) {
  const headingRef = useRef<HTMLHeadingElement>(null)
  const open = useMemo(() => sortAlertsForInbox(unresolvedAlerts(alerts)), [alerts])
  const resolved = useMemo(
    () =>
      alerts
        .filter((a) => a.status === 'resolved')
        .sort((a, b) => (b.resolved_at ?? '').localeCompare(a.resolved_at ?? '')),
    [alerts],
  )
  const now = useNow()

  return (
    <PanelCard
      title="Open alerts"
      count={open.length}
      headingRef={headingRef}
      description="Critical alerts are pinned to the top, then newest first."
    >
      {open.length === 0 ? (
        <p className="text-base text-slate-600">No open alerts.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-slate-200">
          {open.map((alert) => {
            const described = describeAlert(alert, residentsById)
            return (
              <AlertRow
                key={alert.alert_id}
                alert={alert}
                resident={residentsById.get(alert.resident_id)}
                now={now}
                actions={{
                  onAcknowledge: () => {
                    onAcknowledge(alert.alert_id)
                    announce(`Acknowledged: ${described}.`)
                  },
                  onResolve: () => {
                    onResolve(alert.alert_id)
                    announce(`Resolved: ${described}.`)
                    // The row goes away; move focus somewhere stable.
                    headingRef.current?.focus()
                  },
                }}
              />
            )
          })}
        </ul>
      )}

      {resolved.length > 0 && (
        <details className="rounded-xl border border-slate-200 px-4">
          <summary className="flex min-h-11 cursor-pointer items-center text-base font-medium text-slate-800">
            Resolved since this page opened ({resolved.length})
          </summary>
          <ul className="flex flex-col divide-y divide-slate-200 pb-3">
            {resolved.map((alert) => (
              <li key={alert.alert_id} className="flex flex-col gap-1 py-2 text-base text-slate-700">
                <span className="font-medium text-slate-900">{describeAlert(alert, residentsById)}</span>
                {alert.resolved_by && alert.resolved_at && (
                  <span className="text-sm">
                    Resolved by {alert.resolved_by}, <RelativeTime iso={alert.resolved_at} now={now} />
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </PanelCard>
  )
}

function OfflineDevicesPanel({ residents }: { residents: Resident[] }) {
  const devices = offlineDevices(residents)
  const now = useNow()

  return (
    <PanelCard title="Devices offline" count={devices.length}>
      {devices.length === 0 ? (
        <p className="text-base text-slate-600">All devices are online.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-slate-200">
          {devices.map(({ resident, device, kind }) => (
            <li key={device.device_id} className="flex flex-col gap-1.5 py-4 first:pt-0 last:pb-0">
              <p className="inline-flex items-center gap-1.5 text-base font-semibold text-slate-900">
                <WifiOff className="size-5 shrink-0 text-rose-700" aria-hidden="true" />
                {kind === 'wearable' ? 'Wearable' : 'Bedroom sensor'} offline
                <span className="font-normal text-slate-600">· {device.device_id}</span>
              </p>
              <ResidentLine resident={resident} residentId={resident.resident_id} />
              <p className="text-sm text-slate-600">
                Last seen <RelativeTime iso={device.ts} now={now} />
              </p>
            </li>
          ))}
        </ul>
      )}
    </PanelCard>
  )
}

function LowBatteryPanel({ residents }: { residents: Resident[] }) {
  const low = lowBatteryResidents(residents).sort(
    (a, b) => a.wearable.battery_pct - b.wearable.battery_pct,
  )

  return (
    <PanelCard
      title="Wearables low on battery"
      count={low.length}
      description={`Below ${LOW_BATTERY_DISPLAY_PCT}%, lowest first.`}
    >
      {low.length === 0 ? (
        <p className="text-base text-slate-600">No wearables are below {LOW_BATTERY_DISPLAY_PCT}%.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-slate-200">
          {low.map((resident) => (
            <li key={resident.wearable.device_id} className="flex flex-col gap-1.5 py-4 first:pt-0 last:pb-0">
              <p className="flex flex-wrap items-center gap-x-3 text-base font-semibold text-slate-900">
                <BatteryLevel wearable={resident.wearable} />
                <span className="font-normal text-slate-600">{resident.wearable.device_id}</span>
              </p>
              <ResidentLine resident={resident} residentId={resident.resident_id} />
              <p className="text-sm text-slate-700">
                <WearableStatusText wearable={resident.wearable} />
              </p>
            </li>
          ))}
        </ul>
      )}
    </PanelCard>
  )
}

// ---------------------------------------------------------------------------

function PanelCard({
  title,
  count,
  description,
  headingRef,
  children,
}: {
  title: string
  count: number
  description?: string
  headingRef?: Ref<HTMLHeadingElement>
  children: ReactNode
}) {
  const headingId = useId()
  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2
            id={headingId}
            ref={headingRef}
            tabIndex={headingRef ? -1 : undefined}
            className="text-lg font-semibold text-slate-900"
          >
            {title} <span className="font-normal text-slate-600">({count})</span>
          </h2>
          {description && <p className="text-sm text-slate-600">{description}</p>}
        </div>
        {children}
      </section>
    </Card>
  )
}

function NurseSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <LoadingStatus label="Loading dashboard" />
      <StatTilesSkeleton />
      <Card className="flex flex-col gap-4 p-6">
        <Skeleton className="h-6 w-36" />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </Card>
    </div>
  )
}
