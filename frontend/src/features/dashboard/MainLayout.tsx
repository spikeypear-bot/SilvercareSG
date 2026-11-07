import type { ReactNode } from 'react'
import { AppHeader } from '../../components/AppHeader'
import { useDocumentTitle } from '../../lib/useDocumentTitle'
import { useAuth } from '../auth/useAuth'
import { Greeting } from './Greeting'

interface MainLayoutProps {
  /** Header and page title. Never personal data: it also goes in `document.title`. */
  title: string
  /** The greeting under the header (main-page views only). */
  showGreeting?: boolean
  /** Shown above the greeting, e.g. a back link or the staff tabs. */
  before?: ReactNode
  children: ReactNode
}

/**
 * Header and greeting shared by the main-page views and the resident page. No demo-data
 * banner: every view reads the API now (demo data is used only by tests).
 */
export function MainLayout({ title, showGreeting = true, before, children }: MainLayoutProps) {
  useDocumentTitle(title)
  const { user } = useAuth()
  return (
    <div className="min-h-screen bg-slate-50">
      <AppHeader title={title} />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 sm:py-8">
        {before}
        {showGreeting && <Greeting name={user?.name ?? ''} />}
        {children}
      </main>
    </div>
  )
}
