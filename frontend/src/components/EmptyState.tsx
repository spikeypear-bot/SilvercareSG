import type { ReactNode } from 'react'
import emptyIllustration from '../assets/empty-state.svg'

interface EmptyStateProps {
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
      <img src={emptyIllustration} alt="" className="h-28 w-auto" />
      <p className="text-lg font-semibold text-slate-900">{title}</p>
      {description && <p className="max-w-md text-base text-slate-600">{description}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}
