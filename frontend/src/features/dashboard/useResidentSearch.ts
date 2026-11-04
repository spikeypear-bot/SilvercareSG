import { useMemo, useState } from 'react'
import type { Resident, ResidentStatus } from '../../types/dashboard'

export type StatusFilter = 'all' | ResidentStatus

function matchesSearch(resident: Resident, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return resident.name.toLowerCase().includes(q) || resident.resident_id.toLowerCase().includes(q)
}

export interface ResidentSearch {
  query: string
  setQuery: (query: string) => void
  filter: StatusFilter
  setFilter: (filter: StatusFilter) => void
  counts: Record<StatusFilter, number>
  visible: Resident[]
  clear: () => void
}

/** Search-by-name-or-ID and status-filter state. Kept in memory only, never in the URL. */
export function useResidentSearch(residents: Resident[]): ResidentSearch {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<StatusFilter>('all')

  const counts = useMemo(() => {
    const c: Record<StatusFilter, number> = { all: residents.length, urgent: 0, attention: 0, ok: 0 }
    residents.forEach((r) => {
      c[r.status] += 1
    })
    return c
  }, [residents])

  const visible = residents.filter(
    (r) => (filter === 'all' || r.status === filter) && matchesSearch(r, query),
  )

  function clear() {
    setQuery('')
    setFilter('all')
  }

  return { query, setQuery, filter, setFilter, counts, visible, clear }
}
