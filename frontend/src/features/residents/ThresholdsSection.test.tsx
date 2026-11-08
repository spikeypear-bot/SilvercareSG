import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axeViolations } from '../../test/axe'
import type { ResidentThresholds } from '../../types/resident'
import { ThresholdsSection } from './ThresholdsSection'

// Criterion 18 (docs/features/device-alert-limits.md): the critical-limits note.

const SECOND = "The wearable gets the change when you save, or when it next connects if it's offline."

function thresholds(overrides: Partial<ResidentThresholds> = {}): ResidentThresholds {
  return {
    hr_bpm: { min: 50, max: 120 },
    spo2_pct: { min: 92 },
    skin_temp_c: { min: 30, max: 38 },
    ...overrides,
  }
}

function renderSection(t: ResidentThresholds = thresholds(), canEdit = true) {
  const onSave = vi.fn(async () => null as string | null)
  const user = userEvent.setup()
  const view = render(<ThresholdsSection thresholds={t} canEdit={canEdit} onSave={onSave} announce={vi.fn()} now={new Date()} />)
  return { user, onSave, ...view }
}

const note = () => screen.getByText(/^The wearable sends a critical alert/)
const field = (name: string) => screen.getByRole('textbox', { name })
async function setField(user: ReturnType<typeof userEvent.setup>, name: string, value: string) {
  await user.clear(field(name))
  if (value) await user.type(field(name), value)
}

describe('view mode', () => {
  it.each([true, false])('shows the note from the saved thresholds (canEdit %s)', (canEdit) => {
    renderSection(thresholds(), canEdit)
    expect(note()).toHaveTextContent(/^The wearable sends a critical alert below 50 or above 120 bpm, or SpO₂ below 92 %\.$/)
    expect(screen.queryByText(SECOND, { exact: false })).not.toBeInTheDocument()
  })

  it('uses the stricter of each threshold and 45 / 130 / 88 (res-0007-like hr max 140 -> 130)', () => {
    renderSection(thresholds({ hr_bpm: { min: 40, max: 140 }, spo2_pct: { min: 85 } }))
    expect(note()).toHaveTextContent('below 45 or above 130 bpm, or SpO₂ below 88 %.')
  })

  it('falls back to 45 / 130 heart rate when min >= max, keeping SpO2', () => {
    renderSection(thresholds({ hr_bpm: { min: 135, max: 200 }, spo2_pct: { min: 95 } }))
    expect(note()).toHaveTextContent('below 45 or above 130 bpm, or SpO₂ below 95 %.')
  })

  it('comes after the thresholds list', () => {
    renderSection()
    const dl = document.querySelector('dl') as HTMLElement
    expect(dl.compareDocumentPosition(note()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('edit mode', () => {
  it('shows the note from the form values and the second sentence', async () => {
    const { user } = renderSection()
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    expect(note()).toHaveTextContent(
      `The wearable sends a critical alert below 50 or above 120 bpm, or SpO₂ below 92 %. ${SECOND}`,
    )
  })

  it('follows valid form values as the doctor types', async () => {
    const { user } = renderSection()
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    await setField(user, 'Heart rate, upper limit (bpm)', '110')
    await setField(user, 'SpO2, lower limit (%)', '94')
    expect(note()).toHaveTextContent('below 50 or above 110 bpm, or SpO₂ below 94 %.')
    await setField(user, 'Heart rate, upper limit (bpm)', '150')
    expect(note()).toHaveTextContent('below 50 or above 130 bpm, or SpO₂ below 94 %.')
    expect(note()).toHaveTextContent(SECOND)
  })

  it.each([
    ['empty field', 'Heart rate, upper limit (bpm)', ''],
    ['non-number', 'Heart rate, upper limit (bpm)', 'abc'],
    ['out of range', 'SpO2, lower limit (%)', '20'],
    ['hr min >= max', 'Heart rate, lower limit (bpm)', '125'],
    ['invalid skin temperature (not a device limit)', 'Skin temperature, upper limit (°C)', '20.0'],
  ])('shows the saved values while the form is invalid (%s)', async (_, name, value) => {
    const { user } = renderSection()
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    await setField(user, 'SpO2, lower limit (%)', '95') // a valid change first: the note follows it
    expect(note()).toHaveTextContent('SpO₂ below 95 %.')
    await setField(user, name, value)
    expect(note()).toHaveTextContent('below 50 or above 120 bpm, or SpO₂ below 92 %.')
    expect(note()).toHaveTextContent(SECOND)
  })

  it('returns to the saved-values note without the second sentence on Cancel', async () => {
    const { user } = renderSection()
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    await setField(user, 'Heart rate, upper limit (bpm)', '100')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit thresholds' })).toHaveFocus())
    expect(note()).toHaveTextContent(/^The wearable sends a critical alert below 50 or above 120 bpm, or SpO₂ below 92 %\.$/)
  })

  it('is plain text inside the form (no live region, no alert role)', async () => {
    const { user } = renderSection()
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    expect(note().closest('[aria-live],[role="alert"],[role="status"]')).toBeNull()
    expect(within(note().closest('form') as HTMLElement).getByText(/^The wearable sends/)).toBe(note())
  })
})

describe('accessibility', { timeout: 30_000 }, () => {
  it('has no axe violations in view and edit mode', async () => {
    const { container, user } = renderSection()
    expect(await axeViolations(container)).toEqual([])
    await user.click(screen.getByRole('button', { name: 'Edit thresholds' }))
    expect(await axeViolations(container)).toEqual([])
  })
})
