import { useEffect, useMemo, useRef, type Ref } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, House } from 'lucide-react'
import { Avatar } from '../../components/Avatar'
import { Button, buttonBase } from '../../components/Button'
import { Card } from '../../components/Card'
import { EmptyState } from '../../components/EmptyState'
import { Notice } from '../../components/Notice'
import { Skeleton } from '../../components/Skeleton'
import { StatusBadge } from '../../components/StatusBadge'
import { cx } from '../../lib/cx'
import { useNow } from '../../lib/useNow'
import type { Resident } from '../../types/dashboard'
import type { ResidentDetail, VitalsThresholds } from '../../types/resident'
import { useAuth } from '../auth/useAuth'
import { AlertAnnouncer } from '../dashboard/AlertAnnouncer'
import { DashboardError, LoadingStatus } from '../dashboard/DashboardStates'
import { WearableStatusText } from '../dashboard/DeviceStatus'
import { MainLayout } from '../dashboard/MainLayout'
import { StaffOnly, type StaffRole } from '../dashboard/StaffOnly'
import { describeAlert, useAlertAnnouncer } from '../dashboard/useAlertAnnouncer'
import { useResidentDetail } from '../dashboard/useDashboardData'
import { useEscalationWatcher } from '../dashboard/useEscalationWatcher'
import { AlertHistory } from './AlertHistory'
import { DevicesSection } from './DevicesSection'
import { LatestReadings } from './LatestReadings'
import { ThresholdHistory } from './ThresholdHistory'
import { ThresholdsSection } from './ThresholdsSection'

/** Page title for the header and `document.title`: never the resident's name. */
const TITLE = 'Resident details'

/**
 * `/residents/:id` (opaque resident ID). Staff only this phase: nurses and doctors see
 * the page, caregivers get a "for care staff" screen, and users with no group the
 * no-access screen. This only hides things; the API enforces access.
 */
export function ResidentDetailPage() {
  return <StaffOnly>{(role) => <StaffResidentDetail role={role} />}</StaffOnly>
}

function StaffResidentDetail({ role }: { role: StaffRole }) {
  const { id = '' } = useParams()
  const detail = useResidentDetail(id)
  const backRef = useRef<HTMLAnchorElement>(null)

  // Client-side navigation keeps the scroll position and focus of the card that was
  // clicked. Focusing the first control on the page brings both back to the top.
  useEffect(() => {
    backRef.current?.focus()
  }, [id])

  return (
    <MainLayout title={TITLE} showGreeting={false} before={<BackLink ref={backRef} />}>
      {detail.error ? (
        <DashboardError title="We couldn't load this resident." onRetry={detail.retry} />
      ) : detail.loading ? (
        <DetailSkeleton />
      ) : detail.notFound || !detail.data ? (
        <EmptyState
          title="Resident not found"
          description="Check the link, or go back to the dashboard and choose a resident."
        />
      ) : (
        <>
          {detail.actionError && (
            <Notice
              tone="error"
              action={
                <Button variant="secondary" onClick={detail.dismissActionError}>
                  Dismiss
                </Button>
              }
            >
              {detail.actionError}
            </Notice>
          )}
          <DetailContent
            detail={detail.data}
          role={role}
          onAcknowledge={detail.acknowledgeAlert}
          onResolve={detail.resolveAlert}
            onSaveThresholds={detail.updateThresholds}
          />
        </>
      )}
    </MainLayout>
  )
}

function BackLink({ ref }: { ref: Ref<HTMLAnchorElement> }) {
  return (
    <Link ref={ref} to="/" className={cx(buttonBase, 'self-start px-2 text-teal-800 hover:bg-teal-50')}>
      <ArrowLeft className="size-5" aria-hidden="true" />
      Back to dashboard
    </Link>
  )
}

interface DetailContentProps {
  detail: ResidentDetail
  role: 'nurse' | 'doctor'
  onAcknowledge: (alertId: string, ackNote?: string) => Promise<boolean>
  onResolve: (alertId: string, by: string) => void
  onSaveThresholds: (values: VitalsThresholds, by: string) => Promise<string | null>
}

function DetailContent({ detail, role, onAcknowledge, onResolve, onSaveThresholds }: DetailContentProps) {
  const { user } = useAuth()
  // Display only: the API takes who acted from the token's `sub`.
  const staffName = user?.name || 'Staff member'
  const now = useNow()
  const residentsById = useMemo(
    () => new Map([[detail.resident.resident_id, detail.resident]]),
    [detail.resident],
  )
  // The page's one screen-reader announcer, fed from the alert data and the actions.
  const { message, announce } = useAlertAnnouncer(detail.alerts, residentsById)
  useEscalationWatcher(detail.alerts, (alert) =>
    announce(`Escalated to the care centre nurse: ${describeAlert(alert, residentsById)}.`),
  )

  return (
    <>
      <AlertAnnouncer message={message} />
      <ResidentHeader resident={detail.resident} />
      <DevicesSection wearable={detail.wearable} bedroom={detail.bedroom} now={now} />
      <LatestReadings wearable={detail.wearable} bedroom={detail.bedroom} now={now} />
      <AlertHistory
        alerts={detail.alerts}
        resident={detail.resident}
        now={now}
        announce={announce}
        actions={
          role === 'nurse'
            ? {
                onAcknowledge,
                onResolve: (id) => onResolve(id, staffName),
              }
            : undefined
        }
      />
      <ThresholdsSection
        thresholds={detail.thresholds}
        canEdit={role === 'doctor'}
        onSave={(values) => onSaveThresholds(values, staffName)}
        announce={announce}
        now={now}
      />
      <ThresholdHistory history={detail.threshold_history} now={now} />
    </>
  )
}

/** Name, opaque IDs, status (badge + sentence), and the wearable's worn/charging state. */
function ResidentHeader({ resident }: { resident: Resident }) {
  return (
    <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
      <div className="flex min-w-0 items-center gap-4">
        <Avatar name={resident.name} />
        <div className="min-w-0">
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">{resident.name}</h2>
          <p className="flex flex-wrap items-center gap-x-2 text-base text-slate-600">
            <span>{resident.resident_id}</span>
            <span className="inline-flex items-center gap-1">
              · <House className="size-4" aria-hidden="true" />
              <span className="sr-only">Home</span>
              {resident.home_id}
            </span>
          </p>
        </div>
      </div>
      <div className="flex flex-col gap-2 sm:items-end">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge tone={resident.status} />
          <span className="text-base text-slate-700">{resident.status_text}</span>
        </div>
        <span className="text-base text-slate-700">
          <WearableStatusText wearable={resident.wearable} />
        </span>
      </div>
    </Card>
  )
}

function DetailSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <LoadingStatus label="Loading resident" />
      <Card className="flex items-center gap-4 p-6">
        <Skeleton className="size-12 rounded-full" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-5 w-36" />
        </div>
      </Card>
      {[0, 1, 2].map((i) => (
        <Card key={i} className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-24 w-full" />
        </Card>
      ))}
    </div>
  )
}
