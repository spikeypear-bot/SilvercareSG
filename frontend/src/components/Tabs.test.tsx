import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../test/axe'
import { Tabs } from './Tabs'

function renderTabs() {
  render(
    <Tabs
      label="Sections"
      tabs={[
        { id: 'a', label: 'First', panel: <p>Panel one</p> },
        { id: 'b', label: 'Second', panel: <input aria-label="Draft" /> },
        { id: 'c', label: 'Third', panel: <p>Panel three</p> },
      ]}
    />,
  )
  return userEvent.setup()
}

const tab = (name: string) => screen.getByRole('tab', { name })

it('shows the first panel and hides the others, which stay mounted', () => {
  renderTabs()
  expect(tab('First')).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tabpanel', { name: 'First' })).toHaveTextContent('Panel one')
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
  expect(screen.getByText('Panel three')).not.toBeVisible()
})

it('a click switches tabs and a half-filled field keeps its value', async () => {
  const user = renderTabs()
  await user.click(tab('Second'))
  await user.type(screen.getByRole('textbox', { name: 'Draft' }), 'kept')
  await user.click(tab('First'))
  await user.click(tab('Second'))
  expect(screen.getByRole('textbox', { name: 'Draft' })).toHaveValue('kept')
})

it('arrow keys, Home and End move focus and selection; only the selected tab is in the tab order', async () => {
  const user = renderTabs()
  await user.tab()
  expect(tab('First')).toHaveFocus()
  await user.keyboard('{ArrowLeft}')
  expect(tab('Third')).toHaveFocus()
  expect(tab('Third')).toHaveAttribute('aria-selected', 'true')
  await user.keyboard('{ArrowRight}')
  expect(tab('First')).toHaveFocus()
  await user.keyboard('{End}')
  expect(tab('Third')).toHaveAttribute('tabindex', '0')
  expect(tab('First')).toHaveAttribute('tabindex', '-1')
  await user.keyboard('{Home}')
  expect(tab('First')).toHaveAttribute('aria-selected', 'true')
})

it('has no axe violations', async () => {
  const { container } = render(
    <Tabs label="Sections" tabs={[{ id: 'a', label: 'First', panel: <p>Panel one</p> }, { id: 'b', label: 'Second', panel: <p>Two</p> }]} />,
  )
  expect(await axeViolations(container)).toEqual([])
})
