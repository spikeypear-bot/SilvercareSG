import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import type { AlertChange } from '../dashboard/alertChanges'
import { NotificationBell } from './NotificationBell'
import { NotificationsContext, notificationText, scopeChanges, type NotificationsContextValue } from './notifications'
import { NotificationsProvider } from './NotificationsProvider'
import { useNotifications } from './notifications'

const authState = vi.hoisted(() => ({ view: 'nurse' as string }))
vi.mock('../auth/useAuth', () => ({
  useAuth: () => ({ user: { name: 'Test Nurse D', email: 'nurse@example.test', groups: [] }, view: authState.view }),
}))

// Synthetic data only.
const fall: AlertChange = {
  kind: 'new',
  alert: { alert_id: 'a1', resident_id: 'res-0001', alert_type: 'fall', severity: 'critical', status: 'open' },
}
const offline: AlertChange = {
  kind: 'new',
  alert: { alert_id: 'a2', resident_id: 'res-0002', alert_type: 'device_offline', severity: 'warning', status: 'open' },
}

describe('scopeChanges', () => {
  const inputs = [{ change: fall }, { change: offline }]
  it('gives staff everything', () => {
    expect(scopeChanges('nurse', inputs, new Set())).toHaveLength(2)
    expect(scopeChanges('doctor', inputs, new Set())).toHaveLength(2)
  })
  it('gives ops device problems only', () => {
    expect(scopeChanges('ops', inputs, new Set(['res-0001', 'res-0002'])).map((i) => i.change)).toEqual([offline])
  })
  it('gives caregivers only their linked residents', () => {
    expect(scopeChanges('family', inputs, new Set(['res-0002'])).map((i) => i.change)).toEqual([offline])
  })
  it('gives no-access users nothing', () => {
    expect(scopeChanges('no-access', inputs, new Set(['res-0001']))).toEqual([])
  })
})

describe('notificationText', () => {
  it('says critical and device problem in words', () => {
    expect(notificationText('new', fall.alert, 'Fall detected, Test Resident C')).toBe(
      'New critical alert: Fall detected, Test Resident C.',
    )
    expect(notificationText('new', offline.alert, 'Offline, wearable-0002')).toBe(
      'New device problem: Offline, wearable-0002.',
    )
    expect(notificationText('closed', fall.alert, 'x')).toBe('Resolved or cancelled: x.')
    expect(notificationText('closed', offline.alert, 'x')).toBe('Resolved: x.')
  })
})

function Reporter({ onReady }: { onReady: (ctx: NotificationsContextValue) => void }) {
  const ctx = useNotifications()
  if (ctx) onReady(ctx)
  return null
}

function renderBell(view: string): { ctx: () => NotificationsContextValue } {
  authState.view = view
  let current: NotificationsContextValue | null = null
  const wrapper = ({ children }: { children: ReactNode }) => <NotificationsProvider>{children}</NotificationsProvider>
  render(
    <>
      <button type="button">Elsewhere</button>
      <NotificationBell />
      <Reporter onReady={(c) => (current = c)} />
    </>,
    { wrapper },
  )
  return { ctx: () => current as unknown as NotificationsContextValue }
}

describe('NotificationBell', () => {
  it('renders nothing outside the provider', () => {
    const { container } = render(
      <NotificationsContext.Provider value={null}>
        <NotificationBell />
      </NotificationsContext.Provider>,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('counts unseen items in text, marks them seen on open, and never takes focus', async () => {
    const user = userEvent.setup()
    const { ctx } = renderBell('nurse')
    screen.getByRole('button', { name: 'Elsewhere' }).focus()

    act(() => ctx().report([{ change: fall, text: 'New critical alert: Fall detected, Test Resident C.' }], new Set()))
    // The same change again (another poll or page) is not added twice.
    act(() => ctx().report([{ change: fall, text: 'New critical alert: Fall detected, Test Resident C.' }], new Set()))

    const bell = screen.getByRole('button', { name: 'Notifications, 1 new' })
    expect(screen.getByRole('button', { name: 'Elsewhere' })).toHaveFocus()

    await user.click(bell)
    expect(bell).toHaveAttribute('aria-expanded', 'true')
    expect(bell).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Notifications, none new' })).toBe(bell)
    const item = screen.getByText('New critical alert: Fall detected, Test Resident C.').closest('li') as HTMLElement
    expect(within(item).getByText('New')).toBeInTheDocument()

    await user.click(within(item).getByRole('button', { name: /^Dismiss:/ }))
    expect(screen.queryByText('New critical alert: Fall detected, Test Resident C.')).not.toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(bell).toHaveAttribute('aria-expanded', 'false')
    expect(bell).toHaveFocus()
  })

  it('keeps health alerts out of the ops bell', () => {
    const { ctx } = renderBell('ops')
    act(() =>
      ctx().report(
        [
          { change: fall, text: 'New critical alert: fall.' },
          { change: offline, text: 'New device problem: offline.' },
        ],
        new Set(),
      ),
    )
    expect(screen.getByRole('button', { name: 'Notifications, 1 new' })).toBeInTheDocument()
  })
})
