import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'
import { AppRoutes } from '../AppRoutes'
import { AuthContext, type AuthContextValue } from '../features/auth/authContext'
import { AuthProvider } from '../features/auth/AuthProvider'
import { roleLabel, viewForGroups } from '../lib/roles'
import type { AuthUser } from '../types/auth'
import { LocationDisplay } from './LocationDisplay'

/**
 * Full app routes with the real AuthProvider. `src/lib/auth.ts` must be mocked by the
 * calling test file.
 */
export function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
      <LocationDisplay />
    </MemoryRouter>,
  )
}

interface AuthOptions {
  user?: AuthUser
  signOut?: () => Promise<void>
  path?: string
}

/** Renders `ui` as a signed-in user with a fixed auth context (no AuthProvider). */
export function renderWithAuth(ui: ReactElement, options: AuthOptions = {}) {
  const user = options.user ?? { name: 'Test Nurse A', email: 'test.nurse.a@example.test', groups: ['nurse'] }
  const value: AuthContextValue = {
    status: 'signedIn',
    user,
    view: viewForGroups(user.groups),
    role: roleLabel(user.groups),
    refresh: vi.fn(async () => {}),
    signOut: options.signOut ?? vi.fn(async () => {}),
  }
  const result = render(
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={[options.path ?? '/']}>
        <Routes>
          <Route path="/login" element={<p>Login screen</p>} />
          <Route path="*" element={ui} />
        </Routes>
        <LocationDisplay />
      </MemoryRouter>
    </AuthContext.Provider>,
  )
  return { ...result, auth: value }
}
