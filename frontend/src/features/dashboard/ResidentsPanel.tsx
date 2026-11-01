import { useId } from 'react'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { EmptyState } from '../../components/EmptyState'
import { useNow } from '../../lib/useNow'
import type { Resident } from '../../types/dashboard'
import { ResidentCard } from './ResidentCard'
import { ResidentSearchFilter } from './ResidentSearchFilter'
import { useResidentSearch } from './useResidentSearch'

interface ResidentsPanelProps {
  residents: Resident[]
  /** Sparklines on each card (doctor view). */
  showTrends?: boolean
}

/** Residents heading, search and status filter, and the card grid with its empty states. */
export function ResidentsPanel({ residents, showTrends = false }: ResidentsPanelProps) {
  const headingId = useId()
  const search = useResidentSearch(residents)
  const now = useNow()

  return (
    <Card className="p-5 sm:p-6">
      <section aria-labelledby={headingId} className="flex flex-col gap-5">
        <h2 id={headingId} className="text-lg font-semibold text-slate-900">
          Residents
        </h2>

        {residents.length === 0 ? (
          <EmptyState title="No residents yet" description="Residents appear here once they are added." />
        ) : (
          <>
            <ResidentSearchFilter search={search} total={residents.length} />
            {search.visible.length === 0 ? (
              <EmptyState
                title="No residents match your search"
                description="Try a different name or resident ID, or clear the filters."
                action={
                  <Button variant="secondary" onClick={search.clear}>
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <ul className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                {search.visible.map((resident) => (
                  <li key={resident.resident_id}>
                    <ResidentCard resident={resident} now={now} showTrends={showTrends} />
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    </Card>
  )
}
