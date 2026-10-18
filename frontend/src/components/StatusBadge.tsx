import { CircleCheck, Info, OctagonAlert, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { AlertSeverity, ResidentStatus } from '../types/dashboard'
import { cx } from '../lib/cx'

export type BadgeTone = ResidentStatus | AlertSeverity

const TONES: Record<BadgeTone, { label: string; Icon: LucideIcon; className: string }> = {
  ok: { label: 'OK', Icon: CircleCheck, className: 'bg-emerald-50 text-emerald-800 ring-emerald-200' },
  attention: {
    label: 'Attention',
    Icon: TriangleAlert,
    className: 'bg-amber-50 text-amber-900 ring-amber-300',
  },
  urgent: { label: 'Urgent', Icon: OctagonAlert, className: 'bg-rose-50 text-rose-800 ring-rose-200' },
  info: { label: 'Info', Icon: Info, className: 'bg-sky-50 text-sky-900 ring-sky-200' },
  warning: { label: 'Warning', Icon: TriangleAlert, className: 'bg-amber-50 text-amber-900 ring-amber-300' },
  critical: { label: 'Critical', Icon: OctagonAlert, className: 'bg-rose-50 text-rose-800 ring-rose-200' },
}

interface StatusBadgeProps {
  tone: BadgeTone
  /** Overrides the default text label. */
  label?: string
  className?: string
}

/** Status or severity, always with an icon and a text label (never colour alone). */
export function StatusBadge({ tone, label, className }: StatusBadgeProps) {
  const { label: defaultLabel, Icon, className: toneClass } = TONES[tone]
  return (
    <span
      data-tone={tone}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-semibold ring-1 ring-inset',
        toneClass,
        className,
      )}
    >
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <span>{label ?? defaultLabel}</span>
    </span>
  )
}
