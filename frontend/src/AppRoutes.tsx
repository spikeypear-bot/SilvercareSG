import { Navigate, Route, Routes } from 'react-router-dom'
import { AccountPage } from './features/account/AccountPage'
import { ForgotPasswordPage } from './features/auth/ForgotPasswordPage'
import { LoginPage } from './features/auth/LoginPage'
import { RequireAuth, SignedOutOnly } from './features/auth/routeGuards'
import { MainPage } from './features/dashboard/MainPage'

/** Routes. Guards only decide what to show; the API enforces access. */
export function AppRoutes() {
  return (
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
  )
}
