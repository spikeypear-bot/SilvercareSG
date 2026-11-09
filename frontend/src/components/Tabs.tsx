import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { cx } from '../lib/cx'

export interface TabItem {
  id: string
  label: ReactNode
  panel: ReactNode
}

/**
 * WAI-ARIA tabs: click, or arrow keys / Home / End once a tab has focus. Every panel
 * stays mounted (hidden when not selected), so a half-filled form keeps its values and
 * focus moves after actions still find their targets.
 */
export function Tabs({ label, tabs }: { label: string; tabs: TabItem[] }) {
  const baseId = useId()
  const [selected, setSelected] = useState(tabs[0]?.id)
  const buttons = useRef<Array<HTMLButtonElement | null>>([])
  const current = tabs.some((t) => t.id === selected) ? selected : tabs[0]?.id

  function onKeyDown(event: KeyboardEvent, index: number) {
    const last = tabs.length - 1
    const keys: Record<string, number> = {
      ArrowRight: index === last ? 0 : index + 1,
      ArrowLeft: index === 0 ? last : index - 1,
      Home: 0,
      End: last,
    }
    const next = keys[event.key]
    if (next === undefined) return
    event.preventDefault()
    setSelected(tabs[next].id)
    buttons.current[next]?.focus()
  }

  return (
    <div className="flex flex-col gap-6">
      <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b border-slate-200">
        {tabs.map((tab, i) => {
          const active = tab.id === current
          return (
            <button
              key={tab.id}
              ref={(el) => {
                buttons.current[i] = el
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              aria-selected={active}
              aria-controls={`${baseId}-panel-${tab.id}`}
              tabIndex={active ? 0 : -1}
              onClick={() => setSelected(tab.id)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={cx(
                '-mb-px min-h-11 rounded-t-md border-b-2 px-4 py-2 text-base font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700',
                active ? 'border-teal-700 text-teal-800' : 'border-transparent text-slate-600 hover:text-slate-900',
              )}
            >
              {tab.label}
            </button>
          )
        })}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`${baseId}-panel-${tab.id}`}
          aria-labelledby={`${baseId}-tab-${tab.id}`}
          hidden={tab.id !== current}
          className={tab.id === current ? 'flex flex-col gap-6' : 'hidden'}
        >
          {tab.panel}
        </div>
      ))}
    </div>
  )
}
