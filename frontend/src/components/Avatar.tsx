import { initials } from '../lib/format'
import { cx } from '../lib/cx'

/** Initials avatar. Decorative: the name is always shown next to it. */
export function Avatar({ name, size = 'md' }: { name: string; size?: 'md' | 'lg' }) {
  return (
    <span
      aria-hidden="true"
      className={cx(
        'inline-flex shrink-0 items-center justify-center rounded-full bg-teal-100 font-semibold text-teal-900',
        size === 'lg' ? 'size-14 text-xl' : 'size-11 text-base',
      )}
    >
      {initials(name)}
    </span>
  )
}
