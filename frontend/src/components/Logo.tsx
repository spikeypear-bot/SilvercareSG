import logoMark from '../assets/logo-mark.svg'
import { cx } from '../lib/cx'

interface LogoProps {
  /** 'light' for use on the teal brand panel. */
  tone?: 'dark' | 'light'
  size?: 'md' | 'lg'
  /** Hide the wordmark below the `sm` breakpoint (narrow headers). */
  compact?: boolean
  className?: string
}

/** SilverCare logo mark plus wordmark. The wordmark is live text so it uses Inter. */
export function Logo({ tone = 'dark', size = 'md', compact = false, className }: LogoProps) {
  return (
    <span className={cx('inline-flex items-center gap-2.5', className)}>
      <img
        src={logoMark}
        alt=""
        className={cx(size === 'lg' ? 'size-11' : 'size-9', tone === 'light' && 'rounded-xl ring-2 ring-white/70')}
      />
      <span
        className={cx(
          'font-bold tracking-tight',
          compact && 'hidden sm:inline',
          size === 'lg' ? 'text-2xl' : 'text-xl',
          tone === 'light' ? 'text-white' : 'text-slate-900',
        )}
      >
        Silver<span className={tone === 'light' ? 'text-teal-100' : 'text-teal-700'}>Care</span>
      </span>
    </span>
  )
}
