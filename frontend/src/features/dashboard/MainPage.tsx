import { useAuth } from '../auth/useAuth'
import { FamilyView } from './FamilyView'
import { NoAccessPage } from './NoAccessPage'
import { StaffDashboard } from './StaffDashboard'

/** `/`: picks the view from the user's groups. This only hides things; the API enforces access. */
export function MainPage() {
  const { view } = useAuth()
  if (view === 'staff') return <StaffDashboard />
  if (view === 'family') return <FamilyView />
  return <NoAccessPage />
}
