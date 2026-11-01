import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Avatar } from '../../components/Avatar'
import { RelativeTime } from '../../components/RelativeTime'
import { StatusBadge } from '../../components/StatusBadge'
import type { Resident, VitalsReading } from '../../types/dashboard'
import { BatteryLevel, WearableStatusText } from './DeviceStatus'
import { formatHr, formatSpo2, formatTemp } from './labels'
import { Sparkline } from './Sparkline'

interface ResidentCardProps {
  resident: Resident
  now: Date
  /** Adds a sparkline of the latest 5 readings under each vital (clinical review). */
  showTrends?: boolean
}

type VitalKey = 'hr_bpm' | 'spo2_pct' | 'skin_temp_c'

const VITALS: ReadonlyArray<{ key: VitalKey; label: string; format: (v: number | null) => string }> = [
  { key: 'hr_bpm', label: 'Heart rate', format: formatHr },
  { key: 'spo2_pct', label: 'SpO2', format: formatSpo2 },
  { key: 'skin_temp_c', label: 'Skin temp', format: formatTemp },
]

export function ResidentCard({ resident, now, showTrends = false }: ResidentCardProps) {
  const latest = resident.vitals[0]
  const lastUpdated =
    latest && latest.ts > resident.wearable.ts ? latest.ts : resident.wearable.ts
  // vitals are newest first; sparklines read oldest to newest.
  const oldestFirst = [...resident.vitals].reverse()

  return (
    <article className="relative flex h-full flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-4 hover:border-teal-600 hover:bg-slate-50">
      <div className="flex items-start gap-3">
        <Avatar name={resident.name} />
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold text-slate-900">
            {/* The link's ::after covers the card, so the whole card opens the resident page
                while only the name is the link text. The URL holds the opaque ID only. */}
            <Link
              to={`/residents/${encodeURIComponent(resident.resident_id)}`}
              className="after:absolute after:inset-0 after:rounded-2xl hover:underline focus-visible:outline-none focus-visible:after:ring-4 focus-visible:after:ring-teal-600/40"
            >
              {resident.name}
            </Link>
          </h3>
          <p className="text-sm text-slate-600">{resident.resident_id}</p>
        </div>
        <StatusBadge tone={resident.status} />
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-base">
        {VITALS.map(({ key, label, format }) => (
          <Metric
            key={key}
            label={label}
            value={format(latest?.[key] ?? null)}
            trend={showTrends ? <VitalTrend readings={oldestFirst} vital={key} format={format} /> : null}
          />
        ))}
        <Metric label="Battery" value={<BatteryLevel wearable={resident.wearable} />} />
      </dl>
      <div className="mt-auto flex flex-col gap-1 border-t border-slate-100 pt-3 text-sm text-slate-700">
        <WearableStatusText wearable={resident.wearable} />
        <p className="text-slate-600">
          Last updated <RelativeTime iso={lastUpdated} now={now} />
        </p>
      </div>
    </article>
  )
}

function Metric({ label, value, trend }: { label: string; value: ReactNode; trend?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-slate-600">{label}</dt>
      <dd className="font-semibold text-slate-900">{value}</dd>
      {trend && <dd>{trend}</dd>}
    </div>
  )
}

function VitalTrend({
  readings,
  vital,
  format,
}: {
  readings: VitalsReading[]
  vital: VitalKey
  format: (v: number | null) => string
}) {
  const values = readings.map((r) => r[vital])
  return (
    <>
      <Sparkline values={values} width={96} height={28} />
      <span className="sr-only">
        Last {values.length} readings, oldest first: {values.map(format).join(', ')}.
      </span>
    </>
  )
}
