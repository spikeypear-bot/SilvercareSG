import { House } from 'lucide-react'
import type { Resident } from '../../types/dashboard'

/** "Test Resident C · res-0003 · home-0011" (opaque IDs only; no address is stored). */
export function ResidentLine({ resident, residentId }: { resident: Resident | undefined; residentId: string }) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-base text-slate-700">
      <span>{resident?.name ?? 'Unknown resident'}</span>
      <span className="text-slate-600">· {residentId}</span>
      {resident && (
        <span className="inline-flex items-center gap-1 text-slate-600">
          · <House className="size-4" aria-hidden="true" />
          <span className="sr-only">Home</span>
          {resident.home_id}
        </span>
      )}
    </p>
  )
}
