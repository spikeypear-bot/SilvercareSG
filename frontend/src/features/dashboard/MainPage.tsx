import { useAuth } from '../auth/useAuth'
import { DoctorDashboard } from './DoctorDashboard'
import { CaregiverView } from './CaregiverView'
import { NoAccessPage } from './NoAccessPage'
import { NurseDashboard } from './NurseDashboard'
import { OpsDashboard } from '../ops/OpsDashboard'

/** `/`: picks the view from the user's groups. This only hides things; the API enforces access. */
export function MainPage() {
  const { view } = useAuth()
  if (view === 'nurse') return <NurseDashboard />
  if (view === 'doctor') return <DoctorDashboard />
  if (view === 'ops') return <OpsDashboard />
  if (view === 'family') return <CaregiverView />
  return <NoAccessPage />
}
