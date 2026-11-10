import { lazy, Suspense } from 'react'
import { PageLoading } from '../../components/PageLoading'
import { useAuth } from '../auth/useAuth'
import { NoAccessPage } from './NoAccessPage'

// Each role's view is its own download, so a caregiver never loads the nurse's map
// (Leaflet) or the ops screens.
const NurseDashboard = lazy(() => import('./NurseDashboard').then((m) => ({ default: m.NurseDashboard })))
const DoctorDashboard = lazy(() => import('./DoctorDashboard').then((m) => ({ default: m.DoctorDashboard })))
const CaregiverView = lazy(() => import('./CaregiverView').then((m) => ({ default: m.CaregiverView })))
const OpsDashboard = lazy(() => import('../ops/OpsDashboard').then((m) => ({ default: m.OpsDashboard })))

/** `/`: picks the view from the user's groups. This only hides things; the API enforces access. */
export function MainPage() {
  const { view } = useAuth()
  const page =
    view === 'nurse' ? <NurseDashboard /> :
    view === 'doctor' ? <DoctorDashboard /> :
    view === 'ops' ? <OpsDashboard /> :
    view === 'family' ? <CaregiverView /> :
    null
  if (page === null) return <NoAccessPage />
  return <Suspense fallback={<PageLoading />}>{page}</Suspense>
}
