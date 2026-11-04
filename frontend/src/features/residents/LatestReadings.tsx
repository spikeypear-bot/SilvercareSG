import { cx } from '../../lib/cx'
import type { BedroomDevice, VitalsEntry, WearableDevice } from '../../types/resident'
import { formatActivity, formatHr, formatSpo2, formatTemp } from '../dashboard/labels'
import { Sparkline } from '../dashboard/Sparkline'
import { DetailSection } from './DetailSection'
import { ReadingsTable, type ReadingsColumn } from './ReadingsTable'

interface LatestReadingsProps {
  wearable: WearableDevice | null
  bedroom: BedroomDevice | null
  now: Date
}

type VitalKey = 'hr_bpm' | 'spo2_pct' | 'skin_temp_c' | 'activity_pct'

const VITALS: ReadonlyArray<{ key: VitalKey; label: string; format: (v: number | null) => string }> = [
  { key: 'hr_bpm', label: 'Heart rate', format: formatHr },
  { key: 'spo2_pct', label: 'SpO2', format: formatSpo2 },
  { key: 'skin_temp_c', label: 'Skin temp', format: formatTemp },
  { key: 'activity_pct', label: 'Activity', format: formatActivity },
]

const VITALS_COLUMNS: ReadonlyArray<ReadingsColumn<VitalsEntry>> = VITALS.map(({ key, label, format }) => ({
  header: label,
  cell: (row) => format(row[key]),
}))

const yesNo = (value: boolean) => (value ? 'Yes' : 'No')

/**
 * The latest 5 messages of each type for the resident's devices (pattern 11). Only these
 * are stored for the portal, so there is no longer history to show here.
 */
export function LatestReadings({ wearable, bedroom, now }: LatestReadingsProps) {
  return (
    <DetailSection
      title="Latest readings"
      description="The latest 5 messages of each type from each device, newest first."
    >
      {!wearable && !bedroom && <p className="text-base text-slate-600">No devices, so no readings.</p>}

      {wearable && (
        <div className="flex flex-col gap-5">
          <h3 className="text-base font-semibold text-slate-900">Wearable · {wearable.device_id}</h3>
          <VitalTrends readings={wearable.latest.vitals} />
          <ReadingsTable
            caption="Vitals"
            rows={wearable.latest.vitals}
            columns={VITALS_COLUMNS}
            now={now}
            empty="No vitals readings yet. The wearable sends one every minute while it is worn."
          />
          <ReadingsTable
            caption="Falls detected by the wearable"
            rows={wearable.latest.fall}
            columns={[
              { header: 'Confidence', cell: (row) => `${Math.round(row.confidence * 100)}%` },
              { header: 'Peak impact', cell: (row) => `${row.peak_g.toFixed(1)} g` },
              { header: 'Still afterwards', cell: (row) => `${row.still_s} s` },
            ]}
            now={now}
            empty="No falls recorded."
          />
          <ReadingsTable
            caption="Wearable status"
            rows={wearable.latest.status}
            columns={[
              { header: 'Battery', cell: (row) => `${row.battery_pct}%` },
              { header: 'Worn', cell: (row) => yesNo(row.worn) },
              { header: 'Charging', cell: (row) => yesNo(row.charging) },
            ]}
            now={now}
            empty="No status messages yet."
          />
        </div>
      )}

      {bedroom && (
        <div className={cx('flex flex-col gap-5', wearable ? 'border-t border-slate-200 pt-5' : null)}>
          <h3 className="text-base font-semibold text-slate-900">Bedroom sensor · {bedroom.device_id}</h3>
          <div className="grid gap-5 md:grid-cols-2">
            <ReadingsTable
              caption="Motion"
              rows={bedroom.latest.motion}
              columns={[{ header: 'Motion', cell: (row) => (row.motion ? 'Detected' : 'Cleared') }]}
              now={now}
              empty="No motion events yet."
            />
            <ReadingsTable
              caption="Bed"
              rows={bedroom.latest.bed}
              columns={[{ header: 'Bed', cell: (row) => (row.occupied ? 'In bed' : 'Out of bed') }]}
              now={now}
              empty="No bed events yet."
            />
          </div>
          <ReadingsTable
            caption="Bedroom sensor status"
            rows={bedroom.latest.status}
            columns={[
              {
                header: 'Battery',
                cell: (row) => (row.battery_pct === null ? 'Mains powered' : `${row.battery_pct}%`),
              },
            ]}
            now={now}
            empty="No status messages yet."
          />
        </div>
      )}
    </DetailSection>
  )
}

/** Latest value and a sparkline per vital. The table below has the same values as text. */
function VitalTrends({ readings }: { readings: VitalsEntry[] }) {
  if (readings.length === 0) return null
  // Readings are newest first; sparklines read oldest to newest.
  const oldestFirst = [...readings].reverse()
  return (
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {VITALS.map(({ key, label, format }) => {
        const values = oldestFirst.map((r) => r[key])
        return (
          <div key={key} className="flex flex-col gap-1 rounded-xl bg-slate-50 p-3">
            <dt className="text-sm text-slate-600">{label}</dt>
            <dd className="text-lg font-semibold text-slate-900">{format(readings[0][key])}</dd>
            {values.length > 1 && (
              <dd>
                <Sparkline values={values} width={120} height={32} />
                <span className="sr-only">
                  Last {values.length} readings, oldest first: {values.map(format).join(', ')}.
                </span>
              </dd>
            )}
          </div>
        )
      })}
    </dl>
  )
}
