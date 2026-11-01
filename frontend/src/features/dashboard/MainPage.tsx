import { useAuth } from '../auth/useAuth'
import { DoctorDashboard } from './DoctorDashboard'
import { FamilyView } from './FamilyView'
import { NoAccessPage } from './NoAccessPage'
import { NurseDashboard } from './NurseDashboard'

/** `/`: picks the view from the user's groups. This only hides things; the API enforces access. */
export function MainPage() {
  const { view } = useAuth()
  if (view === 'nurse') return <NurseDashboard />
  if (view === 'doctor') return <DoctorDashboard />
  if (view === 'family') return <FamilyView />
  return <NoAccessPage />
}
