import type { ReactNode } from 'react'
import { AppHeader } from '../../components/AppHeader'
import { useDocumentTitle } from '../../lib/useDocumentTitle'
import { useAuth } from '../auth/useAuth'
import { DemoDataBanner } from './DemoDataBanner'
import { Greeting } from './Greeting'

/** Header, demo banner, and greeting shared by both main-page views. */
export function MainLayout({ title, children }: { title: string; children: ReactNode }) {
  useDocumentTitle(title)
  const { user } = useAuth()
  return (
    <div className="min-h-screen bg-slate-50">
      <AppHeader title={title} />
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 sm:py-8">
        <DemoDataBanner />
        <Greeting name={user?.name ?? ''} />
        {children}
      </main>
    </div>
  )
}
