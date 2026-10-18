import { Check, Circle } from 'lucide-react'
import { PASSWORD_RULES } from '../lib/passwordRules'
import { cx } from '../lib/cx'

interface PasswordRulesChecklistProps {
  id: string
  password: string
}

/** Live checklist of the password rules. Link it to the field with aria-describedby. */
export function PasswordRulesChecklist({ id, password }: PasswordRulesChecklistProps) {
  return (
    <div id={id} className="rounded-xl bg-slate-50 p-3">
      <p className="text-sm font-medium text-slate-700">Your password needs:</p>
      <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
        {PASSWORD_RULES.map((rule) => {
          const met = rule.test(password)
          return (
            <li
              key={rule.id}
              data-met={met}
              className={cx(
                'flex items-center gap-2 text-sm',
                met ? 'font-medium text-emerald-800' : 'text-slate-600',
              )}
            >
              {met ? (
                <Check className="size-4 shrink-0" aria-hidden="true" />
              ) : (
                <Circle className="size-4 shrink-0" aria-hidden="true" />
              )}
              <span>{rule.label}</span>
              <span className="sr-only">{met ? '(done)' : '(not yet)'}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
