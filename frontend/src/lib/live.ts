/**
 * Live alerts (docs/features/live-alerts.md, criteria 18-22): subscribe to AppSync Events
 * channels and refetch when a ping arrives. A ping carries only a resident ID; the page
 * refetches through lib/api.ts, which decides what the user may see.
 *
 * - No `VITE_EVENTS_URL` (local dev): nothing connects; pages load and poll as before.
 * - Pings within LIVE_DEBOUNCE_MS share one refetch, started when that time is up.
 * - Every RESUBSCRIBE_MS the subscriptions are closed and opened again with the current
 *   token, so a caregiver unlinked meanwhile is refused within about an hour.
 * - Connect or subscribe failures aren't shown: polling and focus refetches go on.
 */
import { useEffect, useRef } from 'react'
import { events } from 'aws-amplify/data'

export const LIVE_DEBOUNCE_MS = 2_000
export const RESUBSCRIBE_MS = 55 * 60_000

/** Staff dashboards: every resident in both namespaces. */
export const STAFF_CHANNELS: readonly string[] = ['/health/*', '/device/*']

/** One resident's two channels (the resident page, each of a caregiver's residents). */
export function residentChannels(residentId: string): string[] {
  return [`/health/${residentId}`, `/device/${residentId}`]
}

export function liveEnabled(): boolean {
  return Boolean(import.meta.env.VITE_EVENTS_URL)
}

type Closable = { close(): void }

/**
 * Calls `onPing` (debounced) whenever one of `channels` receives an event. The channel
 * list is compared by value, so a new array with the same channels doesn't resubscribe.
 */
export function useLiveRefresh(channels: readonly string[], onPing: () => void): void {
  const onPingRef = useRef(onPing)
  useEffect(() => {
    onPingRef.current = onPing
  })
  const key = channels.join('\n')

  useEffect(() => {
    if (!liveEnabled() || key === '') return
    const paths = key.split('\n')
    let stopped = false
    let open: Closable[] = []
    let pending: ReturnType<typeof setTimeout> | null = null

    function ping() {
      if (pending !== null) return
      pending = setTimeout(() => {
        pending = null
        if (!stopped) onPingRef.current()
      }, LIVE_DEBOUNCE_MS)
    }

    function start() {
      for (const path of paths) {
        events
          .connect(path, { authMode: 'userPool' })
          .then((channel) => {
            if (stopped) {
              channel.close()
              return
            }
            open.push(channel)
            channel.subscribe({
              next: ping,
              // No token or event in the message.
              error: () => console.warn('Live updates unavailable; using polling.'),
            })
          })
          .catch(() => console.warn('Live updates unavailable; using polling.'))
      }
    }

    function closeAll() {
      for (const channel of open) channel.close()
      open = []
    }

    start()
    const resubscribe = setInterval(() => {
      closeAll()
      start()
    }, RESUBSCRIBE_MS)

    return () => {
      stopped = true
      clearInterval(resubscribe)
      if (pending !== null) clearTimeout(pending)
      closeAll()
    }
  }, [key])
}
