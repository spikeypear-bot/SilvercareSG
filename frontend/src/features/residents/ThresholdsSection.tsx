import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Pencil } from 'lucide-react'
import { Button } from '../../components/Button'
import { RelativeTime } from '../../components/RelativeTime'
import { TextField } from '../../components/TextField'
import type { ResidentThresholds, VitalsThresholds } from '../../types/resident'
import { DetailSection } from './DetailSection'
import {
  THRESHOLD_FIELDS,
  THRESHOLD_INPUT_RANGES,
  thresholdsToForm,
  validateThresholds,
  type ThresholdErrors,
  type ThresholdField,
  type ThresholdForm,
} from './thresholds'

interface ThresholdsSectionProps {
  thresholds: ResidentThresholds
  /** Doctors can edit; nurses see the values read-only. */
  canEdit: boolean
  onSave: (values: VitalsThresholds) => void
  announce: (message: string) => void
  now: Date
}

/** The resident's vitals limits (pattern 18). The UI never compares readings with them. */
export function ThresholdsSection({ thresholds, canEdit, onSave, announce, now }: ThresholdsSectionProps) {
  const [editing, setEditing] = useState(false)
  const editButtonRef = useRef<HTMLButtonElement>(null)
  const returnFocus = useRef(false)

  useEffect(() => {
    if (!editing && returnFocus.current) {
      returnFocus.current = false
      editButtonRef.current?.focus()
    }
  }, [editing])

  function close() {
    returnFocus.current = true
    setEditing(false)
  }

  return (
    <DetailSection
      title="Vitals thresholds"
      description="The backend compares each vitals reading with these limits. Demo values, not clinically reviewed."
      action={
        canEdit && !editing ? (
          <Button ref={editButtonRef} variant="secondary" onClick={() => setEditing(true)}>
            <Pencil className="size-5" aria-hidden="true" />
            Edit thresholds
          </Button>
        ) : null
      }
    >
      {editing ? (
        <ThresholdsForm
          initial={thresholds}
          onCancel={close}
          onSave={(values) => {
            onSave(values)
            announce('Thresholds saved.')
            close()
          }}
        />
      ) : (
        <>
          <dl className="grid gap-4 sm:grid-cols-3">
            <Limit label="Heart rate" value={`${thresholds.hr_bpm.min} to ${thresholds.hr_bpm.max} bpm`} />
            <Limit label="SpO2" value={`At least ${thresholds.spo2_pct.min}%`} />
            <Limit
              label="Skin temperature"
              value={`${thresholds.skin_temp_c.min.toFixed(1)} to ${thresholds.skin_temp_c.max.toFixed(1)} °C`}
            />
          </dl>
          <p className="text-sm text-slate-600">
            Last changed by {thresholds.updated_by}, <RelativeTime iso={thresholds.updated_at} now={now} />
          </p>
          {!canEdit && <p className="text-base text-slate-700">Only doctors can change thresholds.</p>}
        </>
      )}
    </DetailSection>
  )
}

function Limit({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-xl bg-slate-50 p-3">
      <dt className="text-sm text-slate-600">{label}</dt>
      <dd className="text-lg font-semibold text-slate-900">{value}</dd>
    </div>
  )
}

function rangeHint(field: ThresholdField): string {
  const spec = THRESHOLD_FIELDS.find((f) => f.field === field)
  if (!spec) return ''
  const { min, max } = THRESHOLD_INPUT_RANGES[spec.metric]
  return spec.decimals === 0
    ? `Whole number from ${min} to ${max}.`
    : `From ${min} to ${max}, up to 1 decimal place.`
}

function ThresholdsForm({
  initial,
  onCancel,
  onSave,
}: {
  initial: VitalsThresholds
  onCancel: () => void
  onSave: (values: VitalsThresholds) => void
}) {
  const [form, setForm] = useState<ThresholdForm>(() => thresholdsToForm(initial))
  const [errors, setErrors] = useState<ThresholdErrors>({})
  const inputRefs = useRef<Partial<Record<ThresholdField, HTMLInputElement | null>>>({})

  // Opening the form moves focus to its first field.
  useEffect(() => {
    inputRefs.current[THRESHOLD_FIELDS[0].field]?.focus()
  }, [])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const result = validateThresholds(form)
    if (result.ok) {
      onSave(result.values)
      return
    }
    setErrors(result.errors)
    const firstInvalid = THRESHOLD_FIELDS.find((f) => result.errors[f.field])
    if (firstInvalid) inputRefs.current[firstInvalid.field]?.focus()
  }

  return (
    <form noValidate onSubmit={handleSubmit} className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-2">
        {THRESHOLD_FIELDS.map((spec) => (
          <TextField
            key={spec.field}
            ref={(el) => {
              inputRefs.current[spec.field] = el
            }}
            label={spec.label}
            hint={rangeHint(spec.field)}
            inputMode={spec.decimals === 0 ? 'numeric' : 'decimal'}
            autoComplete="off"
            value={form[spec.field]}
            error={errors[spec.field]}
            onChange={(e) => {
              const value = e.target.value
              setForm((f) => ({ ...f, [spec.field]: value }))
            }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-3">
        <Button type="submit">Save thresholds</Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
