import { useId, type ReactNode, type Ref } from 'react'
import { Card } from '../../components/Card'

interface DetailSectionProps {
  title: string
  description?: string
  /** Lets a section take focus after an action removes the focused control. */
  headingRef?: Ref<HTMLHeadingElement>
  /** e.g. an Edit button, shown next to the heading. */
  action?: ReactNode
  children: ReactNode
}

/** A card with an h2 heading: one per area of the resident page. */
export function DetailSection({ title, description, headingRef, action, children }: DetailSectionProps) {
  const headingId = useId()
  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <h2
              id={headingId}
              ref={headingRef}
              tabIndex={headingRef ? -1 : undefined}
              className="text-lg font-semibold text-slate-900"
            >
              {title}
            </h2>
            {description && <p className="text-sm text-slate-600">{description}</p>}
          </div>
          {action}
        </div>
        {children}
      </section>
    </Card>
  )
}
