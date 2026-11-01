import { useId, useMemo } from 'react'
import { Card } from '../../components/Card'
import { Skeleton } from '../../components/Skeleton'
import { useNow } from '../../lib/useNow'
import type { DashboardData, Resident, ResidentAlert } from '../../types/dashboard'
import { AlertAnnouncer } from './AlertAnnouncer'
import { AlertRow } from './AlertRow'
import { DashboardError, LoadingStatus } from './DashboardStates'
import { MainLayout } from './MainLayout'
import { ResidentsPanel } from './ResidentsPanel'
import { StatTiles, StatTilesSkeleton } from './StatTile'
import { dashboardStats, sortAlertsForInbox, unresolvedAlerts } from './stats'
import { describeAlert, useAlertAnnouncer } from './useAlertAnnouncer'
import { useDashboardData } from './useDashboardData'
import { useEscalationWatcher } from './useEscalationWatcher'

/** How many of the most urgent open alerts the doctor's summary shows. */
const SUMMARY_ALERTS = 3

/** Doctor view: clinical review. Residents with vitals trends; alerts as a compact summary. */
export function DoctorDashboard() {
  const { data, loading, error, retry } = useDashboardData('staff')

  return (
    <MainLayout title="Doctor dashboard">
      {error ? (
        <DashboardError onRetry={retry} />
      ) : loading || !data ? (
        <DoctorSkeleton />
      ) : (
        <DoctorContent data={data} />
      )}
    </MainLayout>
  )
}

// ---------------------------------------------------------------------------

function DoctorContent({ data }: { data: DashboardData }) {
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
      <StatTiles stats={dashboardStats(data)} />
      <AlertSummary alerts={data.alerts} residentsById={residentsById} />
      <ResidentsPanel residents={data.residents} showTrends />
    </>
  )
}

function AlertSummary({
  alerts,
  residentsById,
}: {
  alerts: ResidentAlert[]
  residentsById: Map<string, Resident>
}) {
  const headingId = useId()
  const open = useMemo(() => sortAlertsForInbox(unresolvedAlerts(alerts)), [alerts])
  const top = open.slice(0, SUMMARY_ALERTS)
  const critical = open.filter((a) => a.severity === 'critical').length
  const acknowledged = open.filter((a) => a.status === 'acknowledged').length
  const now = useNow()

  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 id={headingId} className="text-lg font-semibold text-slate-900">
            Open alerts <span className="font-normal text-slate-600">({open.length})</span>
          </h2>
          <p className="text-sm text-slate-600">
            {critical} critical · {acknowledged} acknowledged. Nurses respond to alerts
            {top.length === 1 && '; the most urgent is shown here'}
            {top.length > 1 && `; the ${top.length} most urgent are shown here`}.
          </p>
        </div>
        {top.length === 0 ? (
          <p className="text-base text-slate-600">No open alerts.</p>
        ) : (
          <ul className="grid gap-4 md:grid-cols-3">
            {top.map((alert) => (
              <AlertRow
                key={alert.alert_id}
                alert={alert}
                resident={residentsById.get(alert.resident_id)}
                now={now}
                variant="tile"
              />
            ))}
          </ul>
        )}
      </section>
    </Card>
  )
}

function DoctorSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <LoadingStatus label="Loading dashboard" />
      <StatTilesSkeleton />
      <Card className="flex flex-col gap-4 p-6">
        <Skeleton className="h-6 w-36" />
        <div className="grid gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32 w-full rounded-xl" />
          ))}
        </div>
      </Card>
      <Card className="flex flex-col gap-4 p-6">
        <Skeleton className="h-6 w-28" />
        <Skeleton className="h-11 w-full" />
        <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-56 w-full rounded-2xl" />
          ))}
        </div>
      </Card>
    </div>
  )
}
