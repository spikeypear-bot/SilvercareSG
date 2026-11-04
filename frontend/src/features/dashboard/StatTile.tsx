import type { ReactNode } from 'react'
import { BatteryLow, Bell, Users, WifiOff } from 'lucide-react'
import { Card } from '../../components/Card'
import { Skeleton } from '../../components/Skeleton'
import { cx } from '../../lib/cx'
import { LOW_BATTERY_DISPLAY_PCT } from './labels'
import type { DashboardStats } from './stats'

interface StatTileProps {
  label: string
  value: number
  detail?: string
  icon: ReactNode
  /** When set, the tile is a toggle button that selects what the panel below shows. */
  onSelect?: () => void
  selected?: boolean
}

/** One summary number. Spans, not paragraphs, so the same markup is valid inside a button. */
export function StatTile({ label, value, detail, icon, onSelect, selected = false }: StatTileProps) {
  const body = (
    <>
      <span
        className={cx(
          'inline-flex size-11 shrink-0 items-center justify-center rounded-xl',
          selected ? 'bg-teal-700 text-white' : 'bg-teal-50 text-teal-800',
        )}
      >
        {icon}
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="text-base text-slate-600">{label}</span>
        <span className="text-3xl font-bold tracking-tight text-slate-900">{value}</span>
        {detail && <span className="text-sm font-medium text-slate-700">{detail}</span>}
      </span>
    </>
  )

  if (!onSelect) return <Card className="flex items-start gap-4 p-5">{body}</Card>

  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cx(
        'flex w-full items-start gap-4 rounded-2xl border bg-white p-5 text-left shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40',
        selected ? 'border-teal-700 ring-2 ring-teal-700' : 'border-slate-200 hover:bg-slate-50',
      )}
    >
      {body}
    </button>
  )
}

export type StatId = 'residents' | 'alerts' | 'offline' | 'lowBattery'

interface StatTilesProps {
  stats: DashboardStats
  /** With `onSelect`, the tiles are toggle buttons that pick what the panel below shows. */
  selected?: StatId
  onSelect?: (id: StatId) => void
}

/** The four summary tiles shared by the staff dashboards. */
export function StatTiles({ stats, selected, onSelect }: StatTilesProps) {
  const tiles: Array<{ id: StatId; label: string; value: number; detail?: string; icon: ReactNode }> = [
    {
      id: 'residents',
      label: 'Residents monitored',
      value: stats.residents,
      icon: <Users className="size-6" aria-hidden="true" />,
    },
    {
      id: 'alerts',
      label: 'Open alerts',
      value: stats.openAlerts,
      detail: `${stats.criticalAlerts} critical`,
      icon: <Bell className="size-6" aria-hidden="true" />,
    },
    {
      id: 'offline',
      label: 'Devices offline',
      value: stats.devicesOffline,
      icon: <WifiOff className="size-6" aria-hidden="true" />,
    },
    {
      id: 'lowBattery',
      label: 'Wearables low on battery',
      value: stats.lowBattery,
      detail: `Below ${LOW_BATTERY_DISPLAY_PCT}%`,
      icon: <BatteryLow className="size-6" aria-hidden="true" />,
    },
  ]

  return (
    <section aria-labelledby="stats-heading">
      <h2 id="stats-heading" className="sr-only">
        Summary
      </h2>
      <div
        role={onSelect ? 'group' : undefined}
        aria-label={onSelect ? 'Choose what the panel below shows' : undefined}
        className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        {tiles.map(({ id, ...tile }) => (
          <StatTile
            key={id}
            {...tile}
            onSelect={onSelect && (() => onSelect(id))}
            selected={selected === id}
          />
        ))}
      </div>
    </section>
  )
}

export function StatTilesSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {[0, 1, 2, 3].map((i) => (
        <Card key={i} className="flex items-start gap-4 p-5">
          <Skeleton className="size-11 rounded-xl" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-8 w-12" />
          </div>
        </Card>
      ))}
    </div>
  )
}
