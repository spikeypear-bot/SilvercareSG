import type { ReactNode } from 'react'
import { CircleAlert, CircleCheck, Info, TriangleAlert, type LucideIcon } from 'lucide-react'
import { cx } from '../lib/cx'

type Tone = 'info' | 'success' | 'warning' | 'error'

const TONES: Record<Tone, { Icon: LucideIcon; className: string }> = {
  info: { Icon: Info, className: 'border-sky-200 bg-sky-50 text-sky-900' },
  success: { Icon: CircleCheck, className: 'border-emerald-200 bg-emerald-50 text-emerald-900' },
  warning: { Icon: TriangleAlert, className: 'border-amber-300 bg-amber-50 text-amber-950' },
  error: { Icon: CircleAlert, className: 'border-rose-200 bg-rose-50 text-rose-900' },
}

interface NoticeProps {
  tone?: Tone
  title?: string
  children?: ReactNode
  /** Optional action, e.g. a "Try again" button. */
  action?: ReactNode
  className?: string
}

/** Inline message. Errors are announced immediately (role="alert"), others politely. */
export function Notice({ tone = 'info', title, children, action, className }: NoticeProps) {
  const { Icon, className: toneClass } = TONES[tone]
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={cx('flex gap-3 rounded-xl border p-4 text-base', toneClass, className)}
    >
      <Icon className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
      <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          {title && <p className="font-semibold">{title}</p>}
          {children && <div>{children}</div>}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
    </div>
  )
}

/** Same component under the name used in the spec. */
export { Notice as Alert }
