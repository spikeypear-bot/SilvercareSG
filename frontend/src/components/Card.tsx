import type { ComponentPropsWithoutRef } from 'react'
import { cx } from '../lib/cx'

export function Card({ className, ...rest }: ComponentPropsWithoutRef<'div'>) {
  return (
    <div
      className={cx('rounded-2xl border border-slate-200 bg-white shadow-sm', className)}
      {...rest}
    />
  )
}
