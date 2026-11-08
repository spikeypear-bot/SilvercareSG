import { NavLink } from 'react-router-dom'
import { Cpu, LayoutDashboard } from 'lucide-react'
import { cx } from '../../lib/cx'

const LINKS = [
  { to: '/', label: 'Dashboard', Icon: LayoutDashboard },
  { to: '/devices', label: 'Devices', Icon: Cpu },
] as const

/**
 * Tabs of the staff area (nurse and doctor) and of the ops area (the ops dashboard and
 * the devices view). They are links between pages, so the current one is marked with
 * `aria-current="page"` (set by NavLink) and a bar under it, not by colour alone.
 */
export function StaffNav({ label = 'Staff area' }: { label?: string }) {
  return (
    <nav aria-label={label} className="border-b border-slate-200">
      <ul className="flex gap-1">
        {LINKS.map(({ to, label, Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              end
              className={({ isActive }) =>
                cx(
                  '-mb-px inline-flex min-h-11 items-center gap-2 rounded-t-lg border-b-4 px-4 text-base focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-600/40',
                  isActive
                    ? 'border-teal-700 font-semibold text-slate-900'
                    : 'border-transparent text-slate-700 hover:bg-slate-100',
                )
              }
            >
              <Icon className="size-5" aria-hidden="true" />
              {label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
