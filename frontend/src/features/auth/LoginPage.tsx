import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '../../components/Button'
import { Notice } from '../../components/Notice'
import { PasswordField } from '../../components/PasswordField'
import { PasswordRulesChecklist } from '../../components/PasswordRulesChecklist'
import { TextField } from '../../components/TextField'
import {
  AUTH_MESSAGES,
  AuthFlowError,
  completeNewPassword,
  confirmTotpCode,
  signIn,
} from '../../lib/auth'
import { meetsPasswordRules } from '../../lib/passwordRules'
import { useDocumentTitle } from '../../lib/useDocumentTitle'
import type { LoginLocationState, SignInStep } from '../../types/auth'
import { AuthLayout } from './AuthLayout'
import { useAuth } from './useAuth'
import { validateEmail } from './validation'

type Step = 'password' | 'newPassword' | 'totp'

interface FieldErrors {
  email?: string
  password?: string
  newPassword?: string
  confirmPassword?: string
  code?: string
}

const linkClass =
  'font-medium text-teal-800 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40'

function messageFor(error: unknown): string {
  return error instanceof AuthFlowError ? error.message : AUTH_MESSAGES.generic
}

export function LoginPage() {
  useDocumentTitle('Sign in')
  const { refresh } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const locationState = (location.state ?? {}) as LoginLocationState

  const [step, setStep] = useState<Step>('password')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [successNotice, setSuccessNotice] = useState(
    locationState.notice === 'passwordReset'
      ? 'Your password has been reset. Sign in with your new password.'
      : null,
  )

  const checklistId = useId()
  const passwordRef = useRef<HTMLInputElement>(null)
  const newPasswordRef = useRef<HTMLInputElement>(null)
  const codeRef = useRef<HTMLInputElement>(null)
  const isFirstRender = useRef(true)

  // Move focus to the first field when the step changes.
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false
      return
    }
    if (step === 'newPassword') newPasswordRef.current?.focus()
    if (step === 'totp') codeRef.current?.focus()
    if (step === 'password') passwordRef.current?.focus()
  }, [step])

  async function goToStep(next: SignInStep) {
    if (next === 'done') {
      await refresh()
      navigate('/', { replace: true })
      return
    }
    setStep(next === 'newPasswordRequired' ? 'newPassword' : 'totp')
  }

  function backToPassword(message: string | null) {
    setStep('password')
    setPassword('')
    setNewPassword('')
    setConfirmPassword('')
    setCode('')
    setFieldErrors({})
    setFormError(message)
  }

  async function handlePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSuccessNotice(null)
    const errors: FieldErrors = {
      email: validateEmail(email),
      password: password ? undefined : 'Enter your password.',
    }
    setFieldErrors(errors)
    setFormError(null)
    if (errors.email || errors.password) return

    setBusy(true)
    try {
      await goToStep(await signIn(email, password))
    } catch (error) {
      setFormError(messageFor(error))
      setPassword('')
      passwordRef.current?.focus()
    } finally {
      setBusy(false)
    }
  }

  async function handleNewPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const errors: FieldErrors = {
      newPassword: meetsPasswordRules(newPassword)
        ? undefined
        : "Your new password doesn't meet all the rules.",
      confirmPassword:
        confirmPassword === newPassword ? undefined : "The passwords don't match.",
    }
    setFieldErrors(errors)
    setFormError(null)
    if (errors.newPassword || errors.confirmPassword) return

    setBusy(true)
    try {
      await goToStep(await completeNewPassword(newPassword))
    } catch (error) {
      if (error instanceof AuthFlowError && error.kind === 'sessionExpired') {
        backToPassword(error.message)
      } else {
        setFormError(messageFor(error))
      }
    } finally {
      setBusy(false)
    }
  }

  async function handleCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = code.trim()
    const errors: FieldErrors = {
      code: /^\d{6}$/.test(trimmed) ? undefined : 'Enter the 6-digit code from your app.',
    }
    setFieldErrors(errors)
    setFormError(null)
    if (errors.code) return

    setBusy(true)
    try {
      await goToStep(await confirmTotpCode(trimmed))
    } catch (error) {
      if (error instanceof AuthFlowError && error.kind === 'codeMismatch') {
        setFieldErrors({ code: error.message })
        setCode('')
        codeRef.current?.focus()
      } else if (error instanceof AuthFlowError && error.kind === 'sessionExpired') {
        backToPassword(error.message)
      } else {
        setFormError(messageFor(error))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout>
      {step === 'password' && (
        <form noValidate onSubmit={handlePassword} className="flex flex-col gap-5">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">
              Sign in to SilverCare
            </h1>
            <p className="mt-1 text-base text-slate-600">Use the email your care provider registered.</p>
          </div>
          {successNotice && <Notice tone="success">{successNotice}</Notice>}
          {formError && <Notice tone="error">{formError}</Notice>}
          <TextField
            label="Email"
            type="email"
            name="email"
            autoComplete="username"
            inputMode="email"
            autoCapitalize="none"
            spellCheck={false}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={fieldErrors.email}
          />
          <div className="flex flex-col gap-2">
            <PasswordField
              ref={passwordRef}
              label="Password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              error={fieldErrors.password}
            />
            <Link to="/forgot-password" className={`${linkClass} self-start py-2 text-base`}>
              Forgot password?
            </Link>
          </div>
          <Button type="submit" busy={busy} fullWidth>
            Sign in
          </Button>
        </form>
      )}

      {step === 'newPassword' && (
        <form noValidate onSubmit={handleNewPassword} className="flex flex-col gap-5">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">Choose a new password</h1>
            <p className="mt-1 text-base text-slate-600">
              You signed in with a temporary password. Choose a new one to continue.
            </p>
          </div>
          {formError && <Notice tone="error">{formError}</Notice>}
          <PasswordField
            ref={newPasswordRef}
            label="New password"
            name="new-password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            error={fieldErrors.newPassword}
            describedBy={checklistId}
          />
          <PasswordRulesChecklist id={checklistId} password={newPassword} />
          <PasswordField
            label="Confirm new password"
            name="confirm-password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            error={fieldErrors.confirmPassword}
          />
          <Button type="submit" busy={busy} fullWidth>
            Save password and sign in
          </Button>
          <Button variant="ghost" onClick={() => backToPassword(null)} disabled={busy}>
            Back to sign in
          </Button>
        </form>
      )}

      {step === 'totp' && (
        <form noValidate onSubmit={handleCode} className="flex flex-col gap-5">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">Enter your code</h1>
            <p className="mt-1 text-base text-slate-600">
              Open your authenticator app and enter the 6-digit code for SilverCare.
            </p>
          </div>
          {formError && <Notice tone="error">{formError}</Notice>}
          <TextField
            ref={codeRef}
            label="6-digit code"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
            error={fieldErrors.code}
          />
          <Button type="submit" busy={busy} fullWidth>
            Verify
          </Button>
          <Button variant="ghost" onClick={() => backToPassword(null)} disabled={busy}>
            Back to sign in
          </Button>
        </form>
      )}
    </AuthLayout>
  )
}
