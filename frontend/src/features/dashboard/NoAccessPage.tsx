import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { Logo } from '../../components/Logo'
import { useDocumentTitle } from '../../lib/useDocumentTitle'
import { useAuth } from '../auth/useAuth'

export function NoAccessPage() {
  useDocumentTitle('No access')
  const { signOut } = useAuth()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)

  async function handleSignOut() {
    setBusy(true)
    try {
      await signOut()
    } catch {
      // Sign-out failures are non-fatal: local state is cleared either way.
    }
    navigate('/login', { replace: true })
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-8">
      <Card className="flex w-full max-w-md flex-col items-center gap-5 p-8 text-center">
        <Logo size="lg" />
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">
          Your account isn't set up yet
        </h1>
        <p className="text-base text-slate-600">Ask your care provider to assign you a role.</p>
        <Button onClick={handleSignOut} busy={busy}>
          Sign out
        </Button>
      </Card>
    </main>
  )
}
