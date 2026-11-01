import { Search } from 'lucide-react'
import { TextField } from '../../components/TextField'
import { cx } from '../../lib/cx'
import type { ResidentSearch, StatusFilter } from './useResidentSearch'

const FILTERS: Array<{ id: StatusFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'urgent', label: 'Urgent' },
  { id: 'attention', label: 'Attention' },
  { id: 'ok', label: 'OK' },
]

/** Search field, status filter chips with counts, and a polite result count. */
export function ResidentSearchFilter({ search, total }: { search: ResidentSearch; total: number }) {
  return (
    <div className="flex flex-col gap-4">
      <TextField
        label="Search residents"
        type="search"
        placeholder="Name or resident ID"
        autoComplete="off"
        value={search.query}
        onChange={(e) => search.setQuery(e.target.value)}
        endAdornment={<Search className="mr-3 size-5 text-slate-500" aria-hidden="true" />}
      />
      <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-2">
        {FILTERS.map(({ id, label }) => {
          const active = search.filter === id
          return (
            <button
              key={id}
              type="button"
              aria-pressed={active}
              onClick={() => search.setFilter(id)}
              className={cx(
                'inline-flex min-h-11 items-center gap-2 rounded-full border px-4 text-base font-medium transition-colors focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40',
                active
                  ? 'border-teal-700 bg-teal-700 text-white'
                  : 'border-slate-300 bg-white text-slate-800 hover:bg-slate-50',
              )}
            >
              {label}
              <span
                className={cx(
                  'rounded-full px-2 text-sm',
                  active ? 'bg-teal-900 text-white' : 'bg-slate-100 text-slate-700',
                )}
              >
                {search.counts[id]}
              </span>
            </button>
          )
        })}
      </div>
      <p className="text-sm text-slate-600" aria-live="polite">
        Showing {search.visible.length} of {total} residents
      </p>
    </div>
  )
}
