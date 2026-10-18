import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { getCurrentUser, signOut as authSignOut } from '../../lib/auth'
import { roleLabel, viewForGroups } from '../../lib/roles'
import type { AuthUser } from '../../types/auth'
import { AuthContext, type AuthContextValue, type AuthStatus } from './authContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<AuthUser | null>(null)

  const applyUser = useCallback((current: AuthUser | null) => {
    setUser(current)
    setStatus(current ? 'signedIn' : 'signedOut')
  }, [])

  const refresh = useCallback(async () => {
    applyUser(await getCurrentUser().catch(() => null))
  }, [applyUser])

  const signOut = useCallback(async () => {
    try {
      await authSignOut()
    } catch {
      // Revocation failed (e.g. offline). Still treat the user as signed out locally;
      // the error is not surfaced because there is nothing the user can do about it.
    }
    setUser(null)
    setStatus('signedOut')
  }, [])

  useEffect(() => {
    let cancelled = false
    getCurrentUser()
      .catch(() => null)
      .then((current) => {
        if (!cancelled) applyUser(current)
      })
    return () => {
      cancelled = true
    }
  }, [applyUser])

  const value = useMemo<AuthContextValue>(() => {
    const groups = user?.groups ?? []
    return {
      status,
      user,
      view: viewForGroups(groups),
      role: roleLabel(groups),
      refresh,
      signOut,
    }
  }, [status, user, refresh, signOut])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
