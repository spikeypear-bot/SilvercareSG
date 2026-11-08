import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { CircleAlert } from 'lucide-react'
import { Button } from '../../components/Button'
import { useHoldRefresh } from '../../lib/refreshHold'
import { ACK_NOTE_COUNT_FROM, ACK_NOTE_ERRORS, ACK_NOTE_MAX, checkAckNote } from './ackNote'

interface AckNoteFormProps {
  /** "Fall detected, Test Resident C", for the buttons' accessible names. */
  context: string
  /** Acknowledges with the normalised note, or `undefined` for none. */
  onSubmit: (ackNote: string | undefined) => void
  /** Closes the form; the alert stays as it was. */
  onCancel: () => void
}

/**
 * The optional note the care centre nurse adds when acknowledging
 * (docs/features/alert-actions-ui.md, criteria 1-7). Opens with focus in the field.
 * The note is never logged or announced.
 */
export function AckNoteForm({ context, onSubmit, onCancel }: AckNoteFormProps) {
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
    const result = checkAckNote(value)
    if (!result.ok) {
      setError(ACK_NOTE_ERRORS[result.error])
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
      aria-label={`Acknowledge: ${context}`}
      noValidate
      className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3"
    >
      <label htmlFor={fieldId} className="text-base font-medium text-slate-900">
        Note for the resident's caregivers (optional)
      </label>
      <p id={hintId} className="text-base text-slate-600">
        Emailed to this resident's caregivers and shown to them in the portal.
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
          Acknowledge<span className="sr-only">: {context}</span>
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel<span className="sr-only">, don't acknowledge: {context}</span>
        </Button>
      </div>
    </form>
  )
}
