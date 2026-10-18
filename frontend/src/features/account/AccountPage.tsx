import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { QRCodeSVG } from 'qrcode.react'
import { ArrowLeft, KeyRound, ShieldCheck, ShieldOff, Smartphone, UserRound } from 'lucide-react'
import { AppHeader } from '../../components/AppHeader'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { Notice } from '../../components/Notice'
import { PasswordField } from '../../components/PasswordField'
import { PasswordRulesChecklist } from '../../components/PasswordRulesChecklist'
import { Skeleton } from '../../components/Skeleton'
import { TextField } from '../../components/TextField'
import {
  AUTH_MESSAGES,
  AuthFlowError,
  changePassword,
  disableTotp,
  finishTotpSetup,
  isTotpEnabled,
  startTotpSetup,
} from '../../lib/auth'
import { meetsPasswordRules } from '../../lib/passwordRules'
import { useDocumentTitle } from '../../lib/useDocumentTitle'
import type { TotpSetup } from '../../types/auth'
import { useAuth } from '../auth/useAuth'

function messageFor(error: unknown): string {
  return error instanceof AuthFlowError ? error.message : AUTH_MESSAGES.generic
}

function SettingsCard({
  title,
  icon,
  children,
}: {
  title: string
  icon: ReactNode
  children: ReactNode
}) {
  const headingId = useId()
  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-5">
        <h2 id={headingId} className="flex items-center gap-2.5 text-lg font-semibold text-slate-900">
          <span className="inline-flex size-9 items-center justify-center rounded-xl bg-teal-50 text-teal-800">
            {icon}
          </span>
          {title}
        </h2>
        {children}
      </section>
    </Card>
  )
}

export function AccountPage() {
  useDocumentTitle('Account settings')

  return (
    <div className="min-h-screen bg-slate-50">
      <AppHeader title="Account settings" />
      <main className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6 sm:px-6 sm:py-8">
        <Link
          to="/"
          className="inline-flex min-h-11 items-center gap-2 self-start rounded-lg font-medium text-teal-800 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40"
        >
          <ArrowLeft className="size-5" aria-hidden="true" />
          Back to main page
        </Link>
        <ProfileCard />
        <ChangePasswordCard />
        <TwoStepCard />
      </main>
    </div>
  )
}

function ProfileCard() {
  const { user, role } = useAuth()
  const rows: Array<[string, string]> = [
    ['Name', user?.name || 'Not set'],
    ['Email', user?.email || 'Not set'],
    ['Role', role],
  ]
  return (
    <SettingsCard title="Profile" icon={<UserRound className="size-5" aria-hidden="true" />}>
      <dl className="grid gap-4 sm:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-sm font-medium text-slate-600">{label}</dt>
            <dd className="mt-0.5 break-words text-base text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-sm text-slate-600">
        To change your name or email, ask your care provider.
      </p>
    </SettingsCard>
  )
}

function ChangePasswordCard() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<{ current?: string; next?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const checklistId = useId()

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSuccess(null)
    setFormError(null)
    const fieldErrors = {
      current: current ? undefined : 'Enter your current password.',
      next: meetsPasswordRules(next) ? undefined : "Your new password doesn't meet all the rules.",
    }
    setErrors(fieldErrors)
    if (fieldErrors.current || fieldErrors.next) return

    setBusy(true)
    try {
      await changePassword(current, next)
      setCurrent('')
      setNext('')
      setSuccess('Your password has been changed.')
    } catch (error) {
      if (error instanceof AuthFlowError && error.kind === 'incorrectCurrentPassword') {
        setErrors({ current: error.message })
        setCurrent('')
      } else {
        setFormError(messageFor(error))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsCard title="Change password" icon={<KeyRound className="size-5" aria-hidden="true" />}>
      <form noValidate onSubmit={handleSubmit} className="flex flex-col gap-5">
        {success && <Notice tone="success">{success}</Notice>}
        {formError && <Notice tone="error">{formError}</Notice>}
        <PasswordField
          label="Current password"
          name="current-password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          error={errors.current}
        />
        <PasswordField
          label="New password"
          name="new-password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          error={errors.next}
          describedBy={checklistId}
        />
        <PasswordRulesChecklist id={checklistId} password={next} />
        <Button type="submit" busy={busy} className="self-start">
          Change password
        </Button>
      </form>
    </SettingsCard>
  )
}

type MfaState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; enabled: boolean }

function TwoStepCard() {
  const { user } = useAuth()
  const [mfa, setMfa] = useState<MfaState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  // Held in memory only while the setup screen is open; cleared on success or cancel.
  const [setup, setSetup] = useState<TotpSetup | null>(null)
  const [code, setCode] = useState('')
  const [codeError, setCodeError] = useState<string | undefined>()
  const [busy, setBusy] = useState<'start' | 'verify' | 'disable' | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const codeRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let cancelled = false
    isTotpEnabled()
      .then((enabled) => {
        if (!cancelled) setMfa({ status: 'ready', enabled })
      })
      .catch(() => {
        if (!cancelled) setMfa({ status: 'error' })
      })
    return () => {
      cancelled = true
    }
  }, [attempt])

  const retry = useCallback(() => {
    setMfa({ status: 'loading' })
    setAttempt((a) => a + 1)
  }, [])

  useEffect(() => {
    if (setup) codeRef.current?.focus()
  }, [setup])

  async function handleStart() {
    setSuccess(null)
    setFormError(null)
    setBusy('start')
    try {
      setSetup(await startTotpSetup(user?.email ?? ''))
    } catch (error) {
      setFormError(messageFor(error))
    } finally {
      setBusy(null)
    }
  }

  function handleCancel() {
    setSetup(null)
    setCode('')
    setCodeError(undefined)
    setFormError(null)
  }

  async function handleVerify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    const trimmed = code.trim()
    if (!/^\d{6}$/.test(trimmed)) {
      setCodeError('Enter the 6-digit code from your app.')
      return
    }
    setCodeError(undefined)
    setBusy('verify')
    try {
      await finishTotpSetup(trimmed)
      setSetup(null)
      setCode('')
      setMfa({ status: 'ready', enabled: true })
      setSuccess('Two-step verification is on. You will be asked for a code when you sign in.')
    } catch (error) {
      if (error instanceof AuthFlowError && error.kind === 'codeMismatch') {
        setCodeError(error.message)
        setCode('')
      } else {
        setFormError(messageFor(error))
      }
    } finally {
      setBusy(null)
    }
  }

  async function handleDisable() {
    setSuccess(null)
    setFormError(null)
    setBusy('disable')
    try {
      await disableTotp()
      setMfa({ status: 'ready', enabled: false })
      setSuccess('Two-step verification is off.')
    } catch (error) {
      setFormError(messageFor(error))
    } finally {
      setBusy(null)
    }
  }

  return (
    <SettingsCard
      title="Two-step verification"
      icon={<Smartphone className="size-5" aria-hidden="true" />}
    >
      <p className="text-base text-slate-700">
        Use an authenticator app to enter a 6-digit code each time you sign in.
      </p>

      {mfa.status === 'loading' && (
        <div aria-busy="true">
          <span role="status" className="sr-only">
            Loading two-step verification status
          </span>
          <Skeleton className="h-8 w-24" />
        </div>
      )}

      {mfa.status === 'error' && (
        <Notice
          tone="error"
          title="We couldn't load your two-step verification status."
          action={
            <Button variant="secondary" onClick={retry}>
              Try again
            </Button>
          }
        />
      )}

      {mfa.status === 'ready' && (
        <div className="flex flex-col gap-5">
          <p className="flex items-center gap-2 text-base">
            <span className="font-medium text-slate-700">Status:</span>
            {mfa.enabled ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-sm font-semibold text-emerald-800 ring-1 ring-inset ring-emerald-200">
                <ShieldCheck className="size-4" aria-hidden="true" />
                On
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-sm font-semibold text-slate-700 ring-1 ring-inset ring-slate-300">
                <ShieldOff className="size-4" aria-hidden="true" />
                Off
              </span>
            )}
          </p>

          {success && <Notice tone="success">{success}</Notice>}
          {formError && <Notice tone="error">{formError}</Notice>}

          {mfa.enabled && (
            <Button
              variant="danger"
              className="self-start"
              busy={busy === 'disable'}
              onClick={handleDisable}
            >
              Turn off
            </Button>
          )}

          {!mfa.enabled && !setup && (
            <Button className="self-start" busy={busy === 'start'} onClick={handleStart}>
              Set up authenticator app
            </Button>
          )}

          {!mfa.enabled && setup && (
            <form noValidate onSubmit={handleVerify} className="flex flex-col gap-5">
              <ol className="flex list-decimal flex-col gap-2 pl-5 text-base text-slate-700">
                <li>Open your authenticator app and add a new account.</li>
                <li>Scan the QR code, or type in the setup key.</li>
                <li>Enter the 6-digit code the app shows.</li>
              </ol>
              <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center">
                <div className="rounded-2xl border border-slate-200 bg-white p-3">
                  <QRCodeSVG
                    value={setup.uri}
                    size={176}
                    marginSize={2}
                    title="QR code for setting up your authenticator app"
                    role="img"
                    aria-label="QR code for setting up your authenticator app"
                  />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-600">Setup key</p>
                  <code
                    data-testid="totp-secret"
                    className="mt-1 block break-all rounded-lg bg-slate-100 px-3 py-2 font-mono text-base tracking-wider text-slate-900"
                  >
                    {setup.secret}
                  </code>
                </div>
              </div>
              <TextField
                ref={codeRef}
                label="6-digit code"
                name="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                error={codeError}
              />
              <div className="flex flex-wrap gap-3">
                <Button type="submit" busy={busy === 'verify'}>
                  Verify and turn on
                </Button>
                <Button variant="secondary" onClick={handleCancel} disabled={busy === 'verify'}>
                  Cancel
                </Button>
              </div>
            </form>
          )}
        </div>
      )}
    </SettingsCard>
  )
}
