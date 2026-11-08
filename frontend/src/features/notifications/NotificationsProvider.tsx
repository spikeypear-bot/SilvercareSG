import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { useAuth } from '../auth/useAuth'
import {
  MAX_NOTIFICATIONS,
  NotificationsContext,
  scopeChanges,
  type AppNotification,
  type NotificationInput,
  type NotificationsContextValue,
} from './notifications'

const NO_ITEMS: AppNotification[] = []

/**
 * Holds the notification bell's items for the signed-in user, in memory only (cleared on
 * sign-out or another user signing in). Pages feed it through their alert announcer;
 * nothing here calls the API, so the bell adds no requests.
 */
export function NotificationsProvider({ children }: { children: ReactNode }) {
  const { user, view } = useAuth()
  const owner = user ? `${user.email}|${view}` : ''
  const [state, setState] = useState<{ owner: string; items: AppNotification[] }>({ owner, items: [] })

  // Another user, or signed out: start empty.
  if (state.owner !== owner) setState({ owner, items: [] })

  const report = useCallback(
    (inputs: NotificationInput[], linkedResidentIds: ReadonlySet<string>) => {
      const allowed = scopeChanges(view, inputs, linkedResidentIds)
      if (allowed.length === 0) return
      const noticedAt = new Date().toISOString()
      setState((s) => {
        const known = new Set(s.items.map((i) => i.key))
        const added: AppNotification[] = []
        for (const { change, text } of allowed) {
          const key = `${change.kind}:${change.alert.alert_id}`
          if (known.has(key)) continue
          known.add(key)
          added.push({ key, kind: change.kind, alert_id: change.alert.alert_id, noticed_at: noticedAt, text, seen: false })
        }
        if (added.length === 0) return s
        return { ...s, items: [...added.reverse(), ...s.items].slice(0, MAX_NOTIFICATIONS) }
      })
    },
    [view],
  )

  const markAllSeen = useCallback(() => {
    setState((s) =>
      s.items.some((i) => !i.seen) ? { ...s, items: s.items.map((i) => (i.seen ? i : { ...i, seen: true })) } : s,
    )
  }, [])
  const dismiss = useCallback((key: string) => {
    setState((s) => ({ ...s, items: s.items.filter((i) => i.key !== key) }))
  }, [])
  const clearAll = useCallback(() => setState((s) => ({ ...s, items: [] })), [])

  const items = state.owner === owner ? state.items : NO_ITEMS
  const value = useMemo<NotificationsContextValue>(
    () => ({
      items,
      unseenCount: items.filter((i) => !i.seen).length,
      report,
      markAllSeen,
      dismiss,
      clearAll,
    }),
    [items, report, markAllSeen, dismiss, clearAll],
  )

  return <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>
}
