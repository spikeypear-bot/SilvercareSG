import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Button } from '../../components/Button'
import { Notice } from '../../components/Notice'
import { PasswordField } from '../../components/PasswordField'
import { PasswordRulesChecklist } from '../../components/PasswordRulesChecklist'
import { TextField } from '../../components/TextField'
import {
  AUTH_MESSAGES,
  AuthFlowError,
  confirmPasswordReset,
  requestPasswordReset,
} from '../../lib/auth'
import { meetsPasswordRules } from '../../lib/passwordRules'
import { useDocumentTitle } from '../../lib/useDocumentTitle'
import { AuthLayout } from './AuthLayout'
import type { LoginLocationState } from '../../types/auth'
import { validateEmail } from './validation'

type Step = 'email' | 'reset'

interface FieldErrors {
  email?: string
  code?: string
  newPassword?: string
}

const NEUTRAL_SENT = "If an account exists for that email, we've sent a code."

function BackToSignIn() {
  return (
    <Link
      to="/login"
      className="inline-flex min-h-11 items-center gap-2 self-start rounded-lg font-medium text-teal-800 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40"
    >
      <ArrowLeft className="size-5" aria-hidden="true" />
      Back to sign in
    </Link>
  )
}

export function ForgotPasswordPage() {
  useDocumentTitle('Forgot password')
  const navigate = useNavigate()

  const [step, setStep] = useState<Step>('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})

  const checklistId = useId()
  const codeRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (step === 'reset') codeRef.current?.focus()
  }, [step])

  async function handleEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const errors: FieldErrors = { email: validateEmail(email) }
    setFieldErrors(errors)
    setFormError(null)
    if (errors.email) return

    setBusy(true)
    try {
      await requestPasswordReset(email)
      setStep('reset')
    } catch {
      setFormError(AUTH_MESSAGES.generic)
    } finally {
      setBusy(false)
    }
  }

  async function handleReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const errors: FieldErrors = {
      code: code.trim() ? undefined : 'Enter the code from the email.',
      newPassword: meetsPasswordRules(newPassword)
        ? undefined
        : "Your new password doesn't meet all the rules.",
    }
    setFieldErrors(errors)
    setFormError(null)
    if (errors.code || errors.newPassword) return

    setBusy(true)
    try {
      await confirmPasswordReset(email, code, newPassword)
      const state: LoginLocationState = { notice: 'passwordReset' }
      navigate('/login', { replace: true, state })
    } catch (error) {
      if (error instanceof AuthFlowError && error.kind === 'codeInvalidOrExpired') {
        setFieldErrors({ code: error.message })
      } else {
        setFormError(AUTH_MESSAGES.generic)
      }
      setBusy(false)
    }
  }

  return (
    <AuthLayout>
      {step === 'email' && (
        <form noValidate onSubmit={handleEmail} className="flex flex-col gap-5">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">Reset your password</h1>
            <p className="mt-1 text-base text-slate-600">
              Enter your email and we'll send you a code to reset your password.
            </p>
          </div>
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
          <Button type="submit" busy={busy} fullWidth>
            Send code
          </Button>
          <BackToSignIn />
        </form>
      )}

      {step === 'reset' && (
        <form noValidate onSubmit={handleReset} className="flex flex-col gap-5">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">Choose a new password</h1>
          </div>
          <Notice tone="info">{NEUTRAL_SENT}</Notice>
          {formError && <Notice tone="error">{formError}</Notice>}
          <TextField
            ref={codeRef}
            label="Code from the email"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            error={fieldErrors.code}
          />
          <PasswordField
            label="New password"
            name="new-password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            error={fieldErrors.newPassword}
            describedBy={checklistId}
          />
          <PasswordRulesChecklist id={checklistId} password={newPassword} />
          <Button type="submit" busy={busy} fullWidth>
            Reset password
          </Button>
          <BackToSignIn />
        </form>
      )}
    </AuthLayout>
  )
}
