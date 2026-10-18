import { useId, type ComponentPropsWithRef, type ReactNode } from 'react'
import { CircleAlert } from 'lucide-react'
import { cx } from '../lib/cx'

export interface TextFieldProps extends Omit<ComponentPropsWithRef<'input'>, 'id'> {
  label: string
  id?: string
  /** Inline error. Linked to the input with aria-describedby. */
  error?: string
  /** Helper text shown under the label. */
  hint?: string
  /** Extra element IDs that describe the input (e.g. a password checklist). */
  describedBy?: string
  /** Element rendered inside the input on the right (e.g. a show/hide button). */
  endAdornment?: ReactNode
}

export function TextField({
  label,
  id,
  error,
  hint,
  describedBy,
  endAdornment,
  className,
  ...inputProps
}: TextFieldProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const hintId = `${inputId}-hint`
  const errorId = `${inputId}-error`
  const describedByIds =
    [hint ? hintId : null, error ? errorId : null, describedBy ?? null].filter(Boolean).join(' ') ||
    undefined

  return (
    <div className={cx('flex flex-col gap-1.5', className)}>
      <label htmlFor={inputId} className="text-base font-medium text-slate-800">
        {label}
      </label>
      {hint && (
        <p id={hintId} className="text-sm text-slate-600">
          {hint}
        </p>
      )}
      <div className="relative">
        <input
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedByIds}
          className={cx(
            'min-h-11 w-full rounded-xl border bg-white px-3.5 py-2.5 text-base text-slate-900 placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-4',
            error
              ? 'border-rose-600 focus-visible:ring-rose-600/30'
              : 'border-slate-300 focus-visible:border-teal-600 focus-visible:ring-teal-600/30',
            endAdornment ? 'pr-14' : null,
          )}
          {...inputProps}
        />
        {endAdornment && (
          <div className="absolute inset-y-0 right-0 flex items-center pr-1">{endAdornment}</div>
        )}
      </div>
      {error && (
        <p id={errorId} className="flex items-start gap-1.5 text-sm font-medium text-rose-700">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </p>
      )}
    </div>
  )
}
