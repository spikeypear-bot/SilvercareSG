import { cx } from '../lib/cx'

interface SpinnerProps {
  /** Screen-reader text. When omitted, the spinner is decorative (e.g. inside a busy button). */
  label?: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const SIZES = { sm: 'size-4', md: 'size-6', lg: 'size-10' } as const

export function Spinner({ label, size = 'md', className }: SpinnerProps) {
  const svg = (
    <svg
      className={cx('motion-safe:animate-spin', SIZES[size], className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
  if (!label) return svg
  return (
    <span role="status" className="inline-flex items-center gap-2 text-teal-700">
      {svg}
      <span className="sr-only">{label}</span>
    </span>
  )
}
