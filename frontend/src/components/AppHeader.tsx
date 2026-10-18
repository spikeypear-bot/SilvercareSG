import { useEffect, useId, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ChevronDown, LogOut, Settings } from 'lucide-react'
import { useAuth } from '../features/auth/useAuth'
import { cx } from '../lib/cx'
import { Logo } from './Logo'
import { Spinner } from './Spinner'

interface AppHeaderProps {
  /** Page title, e.g. "Staff dashboard". Rendered as the page's h1. */
  title: string
}

const menuItemClass =
  'flex min-h-11 w-full items-center gap-2.5 rounded-lg px-3 text-left text-base text-slate-800 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40'

export function AppHeader({ title }: AppHeaderProps) {
  const { user, role, signOut } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const menuId = useId()
  const containerRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
        buttonRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  async function handleSignOut() {
    setSigningOut(true)
    try {
      await signOut()
    } catch {
      // Sign-out failures are non-fatal: local state is cleared either way.
    }
    navigate('/login', { replace: true })
  }

  const displayName = user?.name || 'Your account'

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3 sm:gap-5">
          <Link
            to="/"
            className="shrink-0 rounded-lg focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40"
          >
            <span className="sr-only">SilverCare home</span>
            <span aria-hidden="true" className="inline-flex">
              <Logo compact />
            </span>
          </Link>
          <span aria-hidden="true" className="hidden h-8 w-px bg-slate-200 sm:block" />
          <h1 className="truncate text-lg font-semibold text-slate-900 sm:text-xl">{title}</h1>
        </div>

        <div ref={containerRef} className="relative">
          <button
            ref={buttonRef}
            type="button"
            aria-expanded={open}
            aria-controls={menuId}
            onClick={() => setOpen((v) => !v)}
            className="flex min-h-11 items-center gap-2 rounded-xl px-2 py-1 text-left hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40 sm:px-3"
          >
            <span className="sr-only">User menu: </span>
            <span className="flex flex-col items-end leading-tight">
              <span className="max-w-[9rem] truncate text-base font-medium text-slate-900 sm:max-w-[14rem]">
                {displayName}
              </span>
              <span className="mt-0.5 rounded-full bg-teal-50 px-2 text-sm font-medium text-teal-800">
                {role}
              </span>
            </span>
            <ChevronDown
              className={cx('size-5 text-slate-600 transition-transform', open && 'rotate-180')}
              aria-hidden="true"
            />
          </button>

          <div
            id={menuId}
            hidden={!open}
            className="absolute right-0 z-20 mt-2 w-64 rounded-2xl border border-slate-200 bg-white p-2 shadow-lg"
          >
            <ul className="flex flex-col gap-1">
              <li>
                <Link to="/account" className={menuItemClass} onClick={() => setOpen(false)}>
                  <Settings className="size-5 text-slate-600" aria-hidden="true" />
                  Account settings
                </Link>
              </li>
              <li>
                <button
                  type="button"
                  className={menuItemClass}
                  onClick={handleSignOut}
                  disabled={signingOut}
                  aria-busy={signingOut || undefined}
                >
                  {signingOut ? (
                    <Spinner size="sm" className="text-slate-600" />
                  ) : (
                    <LogOut className="size-5 text-slate-600" aria-hidden="true" />
                  )}
                  Sign out
                </button>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </header>
  )
}
