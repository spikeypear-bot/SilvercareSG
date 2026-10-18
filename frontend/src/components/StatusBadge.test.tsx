import { render, screen } from '@testing-library/react'
import { StatusBadge, type BadgeTone } from './StatusBadge'

describe('StatusBadge (criterion 22: icon and text, never colour alone)', () => {
  it.each([
    ['ok', 'OK'],
    ['attention', 'Attention'],
    ['urgent', 'Urgent'],
    ['info', 'Info'],
    ['warning', 'Warning'],
    ['critical', 'Critical'],
  ] as Array<[BadgeTone, string]>)('%s shows an icon and the text "%s"', (tone, label) => {
    const { container } = render(<StatusBadge tone={tone} />)
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(container.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it('uses a different icon for each status level', () => {
    const icon = (tone: BadgeTone) => {
      const { container, unmount } = render(<StatusBadge tone={tone} />)
      const markup = container.querySelector('svg')?.innerHTML
      unmount()
      return markup
    }
    expect(new Set([icon('ok'), icon('attention'), icon('urgent')]).size).toBe(3)
  })

  it('accepts a custom label', () => {
    render(<StatusBadge tone="urgent" label="Urgent: check now" />)
    expect(screen.getByText('Urgent: check now')).toBeInTheDocument()
  })
})
