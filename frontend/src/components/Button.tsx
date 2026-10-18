import type { ComponentPropsWithRef } from 'react'
import { cx } from '../lib/cx'
import { Spinner } from './Spinner'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

interface ButtonProps extends ComponentPropsWithRef<'button'> {
  variant?: Variant
  /** Shows a spinner and disables the button while a request is in flight. */
  busy?: boolean
  fullWidth?: boolean
}

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-teal-700 text-white hover:bg-teal-800 disabled:bg-teal-700/60',
  secondary:
    'border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:text-slate-500',
  ghost: 'text-teal-800 hover:bg-teal-50 disabled:text-slate-500',
  danger: 'border border-rose-300 bg-white text-rose-800 hover:bg-rose-50 disabled:text-rose-800/60',
}

export const buttonBase =
  'inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 py-2 text-base font-semibold transition-colors focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40 focus-visible:ring-offset-2 disabled:cursor-not-allowed'

export function Button({
  variant = 'primary',
  busy = false,
  fullWidth = false,
  disabled,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cx(buttonBase, VARIANTS[variant], fullWidth && 'w-full', className)}
      {...rest}
    >
      {busy && <Spinner size="sm" />}
      {children}
    </button>
  )
}
