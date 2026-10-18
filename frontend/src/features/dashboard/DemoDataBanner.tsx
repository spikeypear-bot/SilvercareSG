import { FlaskConical } from 'lucide-react'

export const DEMO_BANNER_TEXT =
  'You are viewing synthetic demo data. No real resident information is shown.'

export function DemoDataBanner() {
  return (
    <div
      className="flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-amber-950"
      aria-label="Demo data"
      role="note"
    >
      <FlaskConical className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
      <p className="text-base">
        <span className="mr-2 inline-block rounded-md bg-amber-200 px-1.5 text-sm font-bold uppercase tracking-wide">
          Demo data
        </span>
        <span>{DEMO_BANNER_TEXT}</span>
      </p>
    </div>
  )
}
