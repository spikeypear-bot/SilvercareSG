import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { CircleAlert } from 'lucide-react'
import { Button } from '../../components/Button'
import { useHoldRefresh } from '../../lib/refreshHold'
import { ACK_NOTE_COUNT_FROM, ACK_NOTE_MAX, RESOLVE_NOTE_ERRORS, checkResolveNote } from './ackNote'

interface ResolveNoteFormProps {
  /** "Fall detected, Test Resident C", for the buttons' accessible names. */
  context: string
  /** Resolves with the normalised closing note (never empty). */
  onSubmit: (resolveNote: string) => void
  /** Closes the form; the alert stays as it was. */
  onCancel: () => void
}

/**
 * The required closing note a nurse adds when resolving a health alert
 * (docs/data-model.md, Track B route: resolve). Mirrors AckNoteForm, but the note can't be
 * left empty: submitting without one shows an inline error and sends nothing. Opens with
 * focus in the field. The note is never logged or announced.
 */
export function ResolveNoteForm({ context, onSubmit, onCancel }: ResolveNoteFormProps) {
  // No background refresh while the note is being written (it could remove this row).
  useHoldRefresh()
  const fieldId = useId()
  const hintId = useId()
  const countId = useId()
  const errorId = useId()
  const fieldRef = useRef<HTMLTextAreaElement>(null)
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fieldRef.current?.focus()
  }, [])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const result = checkResolveNote(value)
    if (!result.ok) {
      setError(RESOLVE_NOTE_ERRORS[result.error])
      fieldRef.current?.focus()
      return
    }
    onSubmit(result.note)
  }

  const showCount = value.length >= ACK_NOTE_COUNT_FROM
  const describedBy = [hintId, showCount ? countId : null, error ? errorId : null].filter(Boolean).join(' ')

  return (
    <form
      onSubmit={submit}
      aria-label={`Resolve: ${context}`}
      noValidate
      className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3"
    >
      <label htmlFor={fieldId} className="text-base font-medium text-slate-900">
        Closing note (required)
      </label>
      <p id={hintId} className="text-base text-slate-600">
        Say how the alert was dealt with. Emailed to this resident's caregivers and kept in the
        resident's health record for their lifetime plus 6 years.
      </p>
      <textarea
        id={fieldId}
        ref={fieldRef}
        value={value}
        onChange={(event) => {
          setValue(event.target.value)
          if (error) setError(null)
        }}
        maxLength={ACK_NOTE_MAX}
        rows={3}
        required
        aria-required="true"
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-base text-slate-900 focus-visible:border-teal-700 focus-visible:ring-4 focus-visible:ring-teal-600/40 focus-visible:outline-none"
      />
      {showCount && (
        <p id={countId} className="text-base text-slate-700">
          {`${value.length} of ${ACK_NOTE_MAX} characters`}
        </p>
      )}
      {error && (
        <p id={errorId} className="inline-flex items-center gap-1.5 text-base font-medium text-rose-800">
          <CircleAlert className="size-5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit">
          Resolve<span className="sr-only">: {context}</span>
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel<span className="sr-only">, don't resolve: {context}</span>
        </Button>
      </div>
    </form>
  )
}
