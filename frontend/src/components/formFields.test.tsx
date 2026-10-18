import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../test/axe'
import { Button } from './Button'
import { Notice } from './Notice'
import { PasswordField } from './PasswordField'
import { TextField } from './TextField'

describe('TextField (criterion 17)', () => {
  it('has a visible label tied to the input', () => {
    render(<TextField label="Email" />)
    const input = screen.getByLabelText('Email')
    expect(screen.getByText('Email').tagName).toBe('LABEL')
    expect(input).not.toHaveAttribute('aria-invalid')
    expect(input).not.toHaveAttribute('aria-describedby')
  })

  it('links hint, error, and extra descriptions with aria-describedby', () => {
    render(
      <>
        <TextField label="Code" hint="Check your email." error="Enter the code." describedBy="extra" />
        <p id="extra">Extra help</p>
      </>,
    )
    const input = screen.getByLabelText('Code')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription('Check your email. Enter the code. Extra help')
  })

  it('shows the error with an icon as well as colour', () => {
    render(<TextField label="Code" error="Enter the code." />)
    const error = screen.getByText('Enter the code.').parentElement
    expect(error?.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })
})

describe('PasswordField (criterion 17)', () => {
  it('toggles visibility with an accessible, stateful button', async () => {
    const user = userEvent.setup()
    render(<PasswordField label="Current password" />)
    const input = screen.getByLabelText('Current password')
    expect(input).toHaveAttribute('type', 'password')
    await user.click(screen.getByRole('button', { name: 'Show current password' }))
    expect(input).toHaveAttribute('type', 'text')
    expect(screen.getByRole('button', { name: 'Hide current password' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('the toggle is not a submit button', () => {
    render(<PasswordField label="Password" />)
    expect(screen.getByRole('button', { name: 'Show password' })).toHaveAttribute('type', 'button')
  })

  it('has no axe violations with an error', async () => {
    const { container } = render(<PasswordField label="Password" error="Enter your password." />)
    expect(await axeViolations(container)).toEqual([])
  })
})

describe('Button busy state (criterion 17)', () => {
  it('is disabled and aria-busy while busy, and keeps its accessible name', () => {
    render(<Button busy>Sign in</Button>)
    const button = screen.getByRole('button', { name: 'Sign in' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
  })

  it('defaults to type="button"', () => {
    render(<Button>Cancel</Button>)
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveAttribute('type', 'button')
  })
})

describe('Notice', () => {
  it('errors are role="alert" with an icon', () => {
    const { container } = render(<Notice tone="error">Something went wrong.</Notice>)
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong.')
    expect(container.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it.each(['info', 'success', 'warning'] as const)('%s notices are role="status"', (tone) => {
    render(<Notice tone={tone}>Hello</Notice>)
    expect(screen.getByRole('status')).toHaveTextContent('Hello')
  })
})
