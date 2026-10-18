import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { Spinner } from '../../components/Spinner'
import { useAuth } from './useAuth'

function FullPageLoading() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <Spinner label="Loading" size="lg" />
    </div>
  )
}

/** Signed-out users are sent to /login. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  if (status === 'loading') return <FullPageLoading />
  if (status === 'signedOut') return <Navigate to="/login" replace />
  return <>{children}</>
}

/** Signed-in users are sent to /. */
export function SignedOutOnly({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  if (status === 'loading') return <FullPageLoading />
  if (status === 'signedIn') return <Navigate to="/" replace />
  return <>{children}</>
}
