import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { PageLoading } from './components/PageLoading'
import { ForgotPasswordPage } from './features/auth/ForgotPasswordPage'
import { LoginPage } from './features/auth/LoginPage'
import { RequireAuth, SignedOutOnly } from './features/auth/routeGuards'
import { MainPage } from './features/dashboard/MainPage'

// Loaded on first visit, so sign-in and each role's main view don't wait for pages the
// user may never open. VitalsTrendsPage carries the chart library (doctors only).
const VitalsTrendsPage = lazy(() =>
  import('./features/residents/VitalsTrendsPage').then((m) => ({ default: m.VitalsTrendsPage })),
)
const DevicesPage = lazy(() => import('./features/devices/DevicesPage').then((m) => ({ default: m.DevicesPage })))
const ResidentDetailPage = lazy(() =>
  import('./features/residents/ResidentDetailPage').then((m) => ({ default: m.ResidentDetailPage })),
)
const AccountPage = lazy(() => import('./features/account/AccountPage').then((m) => ({ default: m.AccountPage })))

/** Routes. Guards only decide what to show; the API enforces access. */
export function AppRoutes() {
  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
      <Route
        path="/login"
        element={
          <SignedOutOnly>
            <LoginPage />
          </SignedOutOnly>
        }
      />
      <Route
        path="/forgot-password"
        element={
          <SignedOutOnly>
            <ForgotPasswordPage />
          </SignedOutOnly>
        }
      />
      <Route
        path="/"
        element={
          <RequireAuth>
            <MainPage />
          </RequireAuth>
        }
      />
      <Route
        path="/devices"
        element={
          <RequireAuth>
            <DevicesPage />
          </RequireAuth>
        }
      />
      <Route
        path="/residents/:id"
        element={
          <RequireAuth>
            <ResidentDetailPage />
          </RequireAuth>
        }
      />
      <Route
        path="/residents/:id/vitals"
        element={
          <RequireAuth>
            <VitalsTrendsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/account"
        element={
          <RequireAuth>
            <AccountPage />
          </RequireAuth>
        }
      />
      <Route
        path="*"
        element={
          <RequireAuth>
            <Navigate to="/" replace />
          </RequireAuth>
        }
      />
      </Routes>
    </Suspense>
  )
}
