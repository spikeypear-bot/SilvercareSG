import { useEffect, useId, useRef, useState } from 'react'
import { Bell, X } from 'lucide-react'
import { RelativeTime } from '../../components/RelativeTime'
import { cx } from '../../lib/cx'
import { useNow } from '../../lib/useNow'
import { useNotifications } from './notifications'

const focusRing = 'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40'

/**
 * The header's notification bell: new alerts, acknowledgements, resolutions, and device
 * problems noticed by this page's polling, scoped to the user's role (see
 * `scopeChanges`). A disclosure, not a menu: opening it marks everything seen but leaves
 * focus on the button, and new items never move focus or open it. The count is text
 * (badge and button label), and unseen items say "New" in words, not only in colour.
 * Renders nothing outside `NotificationsProvider`.
 */
export function NotificationBell() {
  const notifications = useNotifications()
  const [open, setOpen] = useState(false)
  // Items that were unseen when the panel opened: still labelled "New" while it is open.
  const [freshKeys, setFreshKeys] = useState<ReadonlySet<string>>(new Set())
  const panelId = useId()
  const headingId = useId()
  const containerRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const now = useNow()

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      const inside = containerRef.current?.contains(document.activeElement)
      setOpen(false)
      if (inside) buttonRef.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!notifications) return null
  const { items, unseenCount, markAllSeen, dismiss, clearAll } = notifications

  function toggle() {
    if (open) {
      setOpen(false)
      return
    }
    setFreshKeys(new Set(items.filter((i) => !i.seen).map((i) => i.key)))
    markAllSeen()
    setOpen(true)
  }

  function dismissOne(key: string) {
    dismiss(key)
    // The button that had focus is gone: keep focus in the panel.
    headingRef.current?.focus()
  }

  const badge = unseenCount > 9 ? '9+' : String(unseenCount)

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={toggle}
        className={cx('relative flex size-11 items-center justify-center rounded-xl hover:bg-slate-100', focusRing)}
      >
        <Bell className="size-6 text-slate-700" aria-hidden="true" />
        <span className="sr-only">
          {unseenCount > 0 ? `Notifications, ${unseenCount} new` : 'Notifications, none new'}
        </span>
        {unseenCount > 0 && (
          <span
            aria-hidden="true"
            className="absolute -right-0.5 -top-0.5 min-w-6 rounded-full bg-rose-700 px-1.5 text-center text-sm font-semibold leading-6 text-white"
          >
            {badge}
          </span>
        )}
      </button>

      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 z-20 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-2xl border border-slate-200 bg-white p-2 shadow-lg"
      >
        <section aria-labelledby={headingId}>
          <div className="flex items-center justify-between gap-2 px-2 py-1">
            <h2 id={headingId} ref={headingRef} tabIndex={-1} className={cx('rounded text-base font-semibold text-slate-900', focusRing)}>
              Notifications
            </h2>
            {items.length > 0 && (
              <button
                type="button"
                onClick={() => {
                  clearAll()
                  headingRef.current?.focus()
                }}
                className={cx('min-h-11 rounded-lg px-3 text-base font-medium text-teal-800 hover:bg-teal-50', focusRing)}
              >
                Clear all
              </button>
            )}
          </div>
          {items.length === 0 ? (
            <p className="px-2 py-3 text-base text-slate-600">
              Nothing new. Changes to alerts appear here while a dashboard is open.
            </p>
          ) : (
            <ul className="flex max-h-[60vh] flex-col divide-y divide-slate-100 overflow-y-auto">
              {items.map((item) => {
                const isNew = !item.seen || freshKeys.has(item.key)
                return (
                  <li key={item.key} className="flex items-start gap-2 px-2 py-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-base text-slate-900">
                        {isNew && (
                          <span className="mr-1.5 rounded bg-teal-50 px-1.5 text-sm font-semibold text-teal-800">New</span>
                        )}
                        {item.text}
                      </p>
                      <p className="text-sm text-slate-600">
                        <RelativeTime iso={item.noticed_at} now={now} />
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => dismissOne(item.key)}
                      className={cx('flex size-11 shrink-0 items-center justify-center rounded-lg hover:bg-slate-100', focusRing)}
                    >
                      <X className="size-5 text-slate-600" aria-hidden="true" />
                      <span className="sr-only">Dismiss: {item.text}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
