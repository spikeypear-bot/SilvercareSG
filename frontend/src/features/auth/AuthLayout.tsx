import type { ReactNode } from 'react'
import { CircleCheck } from 'lucide-react'
import loginIllustration from '../../assets/login-illustration.svg'
import { Card } from '../../components/Card'
import { Logo } from '../../components/Logo'

const BENEFITS = [
  'Check in on wellbeing at a glance',
  'Clear alerts when something needs attention',
  'Built for families and care teams together',
]

/** Split layout for signed-out pages: brand panel (desktop) + form card. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50 lg:grid lg:grid-cols-2">
      <aside className="hidden flex-col justify-between gap-10 bg-gradient-to-br from-teal-700 via-teal-800 to-teal-950 p-12 text-white lg:flex">
        <Logo tone="light" size="lg" />
        <div className="flex flex-col gap-8">
          <img src={loginIllustration} alt="" className="mx-auto w-full max-w-md" />
          <div>
            <p className="text-3xl font-bold tracking-tight">Peace of mind for every family</p>
            <ul className="mt-6 flex flex-col gap-3 text-lg text-teal-50">
              {BENEFITS.map((benefit) => (
                <li key={benefit} className="flex items-start gap-3">
                  <CircleCheck className="mt-1 size-5 shrink-0 text-teal-200" aria-hidden="true" />
                  <span>{benefit}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
        <p className="text-sm text-teal-100">SilverCare SG</p>
      </aside>

      <div className="flex min-h-screen flex-col lg:min-h-0">
        <header className="border-b border-slate-200 bg-white px-4 py-3 lg:hidden">
          <Logo />
        </header>
        <main className="flex flex-1 flex-col items-center justify-center px-4 py-8 sm:px-6">
          <div className="w-full max-w-md">
            <Card className="p-6 sm:p-8">{children}</Card>
            <p className="mt-6 text-center text-base text-slate-600">
              Accounts are created by your care provider.
            </p>
          </div>
        </main>
      </div>
    </div>
  )
}
