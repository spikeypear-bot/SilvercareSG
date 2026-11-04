import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { buttonBase } from '../../components/Button'
import { Card } from '../../components/Card'
import { cx } from '../../lib/cx'
import { useAuth } from '../auth/useAuth'
import { MainLayout } from './MainLayout'
import { NoAccessPage } from './NoAccessPage'

export type StaffRole = 'nurse' | 'doctor'

/**
 * Staff-only pages: renders `children` for nurses and doctors (nurse wins for a user in
 * both), a "for care staff" notice for caregivers, and the no-access screen for
 * users with no group. Nothing inside loads for non-staff. This only hides things; the
 * API enforces access.
 */
export function StaffOnly({ children }: { children: (role: StaffRole) => ReactNode }) {
  const { view } = useAuth()
  if (view === 'nurse' || view === 'doctor') return <>{children(view)}</>
  if (view === 'family') return <StaffOnlyNotice />
  return <NoAccessPage />
}

/** For signed-in caregivers who reach a staff-only page. Loads no data. */
function StaffOnlyNotice() {
  return (
    <MainLayout title="Staff only" showGreeting={false}>
      <Card className="flex flex-col items-start gap-4 p-6">
        <h2 className="text-xl font-semibold text-slate-900">This page is for care staff.</h2>
        <p className="text-base text-slate-700">
          Your dashboard shows the residents linked to your account.
        </p>
        <Link to="/" className={cx(buttonBase, 'bg-teal-700 text-white hover:bg-teal-800')}>
          <ArrowLeft className="size-5" aria-hidden="true" />
          Back to my dashboard
        </Link>
      </Card>
    </MainLayout>
  )
}
