import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { axeViolations } from '../../test/axe'
import { LocationDisplay } from '../../test/LocationDisplay'
import { StaffNav } from './StaffNav'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <StaffNav />
      <Routes>
        <Route path="/" element={<p>Dashboard screen</p>} />
        <Route path="/devices" element={<p>Devices screen</p>} />
        <Route path="*" element={<p>Other screen</p>} />
      </Routes>
      <LocationDisplay />
    </MemoryRouter>,
  )
}

const nav = () => screen.getByRole('navigation', { name: 'Staff area' })
const link = (name: string) => within(nav()).getByRole('link', { name })

describe('StaffNav', () => {
  it('is a "Staff area" nav with Dashboard and Devices links in a list', () => {
    renderAt('/')
    const items = within(nav()).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(within(nav()).getAllByRole('link').map((l) => l.textContent)).toEqual(['Dashboard', 'Devices'])
    expect(link('Dashboard')).toHaveAttribute('href', '/')
    expect(link('Devices')).toHaveAttribute('href', '/devices')
  })

  it.each([
    ['/', 'Dashboard', 'Devices'],
    ['/devices', 'Devices', 'Dashboard'],
  ])('at %s, %s is the current page and %s is not', (path, current, other) => {
    renderAt(path)
    expect(link(current)).toHaveAttribute('aria-current', 'page')
    expect(link(other)).not.toHaveAttribute('aria-current')
  })

  it('marks neither link current on another page (Dashboard matches "/" exactly)', () => {
    renderAt('/residents/res-0001')
    expect(link('Dashboard')).not.toHaveAttribute('aria-current')
    expect(link('Devices')).not.toHaveAttribute('aria-current')
  })

  it('the current tab is marked by more than colour (a bar and bold text, plus aria-current)', () => {
    renderAt('/devices')
    expect(link('Devices')).toHaveClass('border-teal-700', 'font-semibold')
    expect(link('Dashboard')).toHaveClass('border-transparent')
    expect(link('Dashboard')).not.toHaveClass('font-semibold')
  })

  it('links have visible focus styles and 44px tap targets', () => {
    renderAt('/')
    for (const name of ['Dashboard', 'Devices']) {
      expect(link(name)).toHaveClass('focus-visible:ring-4', 'min-h-11')
      expect(link(name).querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    }
  })

  it('moves between Dashboard and Devices, updating aria-current', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await user.click(link('Devices'))
    expect(screen.getByText('Devices screen')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/devices')
    expect(link('Devices')).toHaveAttribute('aria-current', 'page')
    expect(link('Dashboard')).not.toHaveAttribute('aria-current')

    await user.click(link('Dashboard'))
    expect(screen.getByText('Dashboard screen')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/)
    expect(link('Dashboard')).toHaveAttribute('aria-current', 'page')
  })

  it('can be used with the keyboard', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await user.tab()
    expect(link('Dashboard')).toHaveFocus()
    await user.tab()
    expect(link('Devices')).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(screen.getByTestId('location')).toHaveTextContent('/devices')
  })

  it('has no axe violations', async () => {
    const { container } = renderAt('/devices')
    expect(await axeViolations(container)).toEqual([])
  })
})
