import type { ReactNode } from 'react'
import { RelativeTime } from '../../components/RelativeTime'

export interface ReadingsColumn<T> {
  header: string
  cell: (row: T) => ReactNode
}

interface ReadingsTableProps<T extends { message_id: string; ts: string }> {
  /** Visible caption, e.g. "Vitals". Also names the table for screen readers. */
  caption: string
  rows: readonly T[]
  columns: ReadonlyArray<ReadingsColumn<T>>
  now: Date
  /** Shown instead of the table when there are no rows. */
  empty: string
}

/**
 * The latest (up to 5) messages of one type, newest first, with the time as the first
 * column. Scrolls sideways inside its box on narrow screens, never the page.
 */
export function ReadingsTable<T extends { message_id: string; ts: string }>({
  caption,
  rows,
  columns,
  now,
  empty,
}: ReadingsTableProps<T>) {
  if (rows.length === 0) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-base font-semibold text-slate-900">{caption}</p>
        <p className="text-base text-slate-600">{empty}</p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-left text-base">
        <caption className="pb-2 text-left text-base font-semibold text-slate-900">
          {caption} <span className="font-normal text-slate-600">(latest {rows.length}, newest first)</span>
        </caption>
        <thead>
          <tr className="border-b border-slate-200 text-sm text-slate-600">
            <th scope="col" className="py-2 pr-4 font-medium">
              Time
            </th>
            {columns.map((c) => (
              <th key={c.header} scope="col" className="py-2 pr-4 font-medium">
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.message_id} className="border-b border-slate-100 last:border-0">
              <th scope="row" className="py-2 pr-4 font-normal text-slate-700">
                <RelativeTime iso={row.ts} now={now} />
              </th>
              {columns.map((c) => (
                <td key={c.header} className="py-2 pr-4 text-slate-900">
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
