import { cx } from '../lib/cx'

/** Grey placeholder block shown while data loads. Decorative; pair with a status message. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cx('rounded-lg bg-slate-200 motion-safe:animate-pulse', className)}
    />
  )
}
