import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import { ResolveNoteForm } from './ResolveNoteForm'

// The staff resolve's required closing note (docs/data-model.md, Track B route: resolve).

const CONTEXT = 'Fall detected, Test Resident C'
const HINT =
  "Say how the alert was dealt with. Emailed to this resident's caregivers and kept in the resident's health record for their lifetime plus 6 years."
const EMPTY_ERROR = 'Add a note saying how this alert was dealt with.'
const HIDDEN_ERROR = 'Remove hidden or control characters from the note.'

function setup() {
  const user = userEvent.setup()
  const onSubmit = vi.fn<(note: string) => void>()
  const onCancel = vi.fn<() => void>()
  const view = render(<ResolveNoteForm context={CONTEXT} onSubmit={onSubmit} onCancel={onCancel} />)
  return { user, onSubmit, onCancel, ...view }
}

const form = () => screen.getByRole('form', { name: `Resolve: ${CONTEXT}` })
const field = () => screen.getByRole('textbox', { name: 'Closing note (required)' })
const submitButton = () => screen.getByRole('button', { name: `Resolve: ${CONTEXT}` })
const cancelButton = () => screen.getByRole('button', { name: `Cancel, don't resolve: ${CONTEXT}` })

describe('ResolveNoteForm', () => {
  it('names the form, field, and buttons with the alert context', () => {
    setup()
    expect(form()).toBeInTheDocument()
    expect(field()).toBeInTheDocument()
    expect(submitButton()).toHaveAttribute('type', 'submit')
    expect(cancelButton()).toHaveAttribute('type', 'button')
    // The context is for screen readers only; the visible text is just the verb.
    expect(submitButton().querySelector('.sr-only')).toHaveTextContent(`: ${CONTEXT}`)
    expect(cancelButton().querySelector('.sr-only')).toHaveTextContent(`, don't resolve: ${CONTEXT}`)
  })

  it('the field is required, limited to 500, and described by the hint', () => {
    setup()
    expect(field()).toBeRequired()
    expect(field()).toHaveAttribute('aria-required', 'true')
    expect(field()).toHaveAttribute('maxLength', '500')
    expect(field()).toHaveAccessibleDescription(HINT)
    expect(screen.getByText(HINT)).toBeInTheDocument()
    expect(field()).not.toHaveAttribute('aria-invalid')
  })

  it('opens with focus in the field', async () => {
    setup()
    await waitFor(() => expect(field()).toHaveFocus())
  })

  it.each([
    ['empty', ''],
    ['whitespace only', '   \r\n\t '],
  ])('submitting %s shows the empty error, keeps focus in the field, and does not submit', async (_, value) => {
    const { user, onSubmit } = setup()
    if (value) fireEvent.change(field(), { target: { value } })
    await user.click(submitButton())
    expect(onSubmit).not.toHaveBeenCalled()
    const error = screen.getByText(EMPTY_ERROR)
    expect(field()).toHaveAttribute('aria-invalid', 'true')
    expect(field().getAttribute('aria-describedby')?.split(' ')).toContain(error.id)
    expect(field()).toHaveAccessibleDescription(`${HINT} ${EMPTY_ERROR}`)
    // Text and an icon, not colour alone.
    expect(error.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    await waitFor(() => expect(field()).toHaveFocus())
  })

  it('Enter in an empty form (submit) is blocked the same way', async () => {
    const { onSubmit } = setup()
    fireEvent.submit(form())
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText(EMPTY_ERROR)).toBeInTheDocument()
  })

  it('typing clears the error and aria-invalid', async () => {
    const { user } = setup()
    await user.click(submitButton())
    expect(screen.getByText(EMPTY_ERROR)).toBeInTheDocument()
    await user.type(field(), 'S')
    expect(screen.queryByText(EMPTY_ERROR)).not.toBeInTheDocument()
    expect(field()).not.toHaveAttribute('aria-invalid')
    expect(field()).toHaveAccessibleDescription(HINT)
  })

  it('a valid note submits once, normalised (CRLF to LF, trimmed)', async () => {
    const { user, onSubmit } = setup()
    fireEvent.change(field(), { target: { value: '  Synthetic note: resident checked.\r\nFalse alarm.  ' } })
    await user.click(submitButton())
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith('Synthetic note: resident checked.\nFalse alarm.')
    expect(screen.queryByText(EMPTY_ERROR)).not.toBeInTheDocument()
  })

  it('typed text submits as typed', async () => {
    const { user, onSubmit } = setup()
    await user.type(field(), 'Synthetic note: false alarm.')
    await user.click(submitButton())
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith('Synthetic note: false alarm.')
  })

  it.each([
    ['a line separator', 'Synthetic\u2028note'],
    ['a zero-width space', 'Synthetic\u200Bnote'],
    ['a tab', 'Synthetic\tnote'],
  ])('refuses %s with the hidden-character error', async (_, value) => {
    const { user, onSubmit } = setup()
    fireEvent.change(field(), { target: { value } })
    await user.click(submitButton())
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText(HIDDEN_ERROR)).toBeInTheDocument()
    expect(field()).toHaveAttribute('aria-invalid', 'true')
    await waitFor(() => expect(field()).toHaveFocus())
  })

  it('refuses 501 characters (the field limit is not enforced on paste in every browser)', async () => {
    const { user, onSubmit } = setup()
    fireEvent.change(field(), { target: { value: 'a'.repeat(501) } })
    await user.click(submitButton())
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('Keep the note to 500 characters or fewer.')).toBeInTheDocument()
  })

  it('shows "N of 500 characters" only from 400 characters, linked to the field', () => {
    setup()
    fireEvent.change(field(), { target: { value: 'a'.repeat(399) } })
    expect(screen.queryByText(/of 500 characters/)).not.toBeInTheDocument()
    fireEvent.change(field(), { target: { value: 'a'.repeat(400) } })
    const count = screen.getByText('400 of 500 characters')
    expect(field().getAttribute('aria-describedby')?.split(' ')).toContain(count.id)
  })

  it('Cancel calls onCancel and never onSubmit, even with a draft', async () => {
    const { user, onSubmit, onCancel } = setup()
    await user.type(field(), 'Synthetic draft')
    await user.click(cancelButton())
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('has no axe violations, without and with the empty error showing', async () => {
    const { container, user } = setup()
    expect(await axeViolations(container)).toEqual([])
    await user.click(submitButton())
    expect(screen.getByText(EMPTY_ERROR)).toBeInTheDocument()
    expect(await axeViolations(container)).toEqual([])
  }, 30_000)
})
