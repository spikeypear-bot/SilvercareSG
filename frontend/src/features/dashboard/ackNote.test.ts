import { ACK_NOTE_COUNT_FROM, ACK_NOTE_ERRORS, ACK_NOTE_MAX, checkAckNote, normalizeAckNote } from './ackNote'

// Main's `note` rules (docs/data-model.md, Status updates; alert-actions-ui criteria 3-6).
// Hidden characters are written as escapes so the source stays readable.

describe('normalizeAckNote', () => {
  it('turns \\r\\n into \\n and trims both ends', () => {
    expect(normalizeAckNote('  Line one\r\nLine two \n')).toBe('Line one\nLine two')
  })
})

describe('checkAckNote', () => {
  it.each([[''], ['   '], ['\n\n'], [' \r\n ']])('%j is no note', (raw) => {
    expect(checkAckNote(raw)).toEqual({ ok: true, note: undefined })
  })

  it('accepts a note with newlines, normalised', () => {
    expect(checkAckNote(' Synthetic note: on the way.\r\nAbout 8 minutes. ')).toEqual({
      ok: true,
      note: 'Synthetic note: on the way.\nAbout 8 minutes.',
    })
  })

  it.each([
    ['a tab', 'Synthetic\tnote'],
    ['a zero-width space', 'Synthetic​note'],
    ['a right-to-left override', 'Synthetic ‮note'],
    ['a line separator', 'Synthetic note'],
    ['a paragraph separator', 'Synthetic note'],
    ['a bare carriage return', 'Synthetic\rnote'],
    ['a NUL', 'Synthetic\u0000note'],
    ['a zero-width joiner', 'Synthetic‍note'],
  ])('refuses %s', (_, raw) => {
    expect(checkAckNote(raw)).toEqual({ ok: false, error: 'hidden' })
  })

  it('accepts exactly 500 characters and refuses 501', () => {
    expect(checkAckNote('a'.repeat(ACK_NOTE_MAX))).toEqual({ ok: true, note: 'a'.repeat(500) })
    expect(checkAckNote('a'.repeat(ACK_NOTE_MAX + 1))).toEqual({ ok: false, error: 'tooLong' })
  })

  it('counts characters as the API does (code points), so 500 emoji are accepted', () => {
    const emoji = '\u{1F691}'.repeat(500)
    expect(emoji.length).toBe(1000)
    expect(checkAckNote(emoji)).toEqual({ ok: true, note: emoji })
  })

  it('has user-facing messages for each error, and counts from 400', () => {
    expect(ACK_NOTE_ERRORS.hidden).toBe('Remove hidden or control characters from the note.')
    expect(ACK_NOTE_ERRORS.tooLong).toBe('Keep the note to 500 characters or fewer.')
    expect(ACK_NOTE_COUNT_FROM).toBe(400)
  })
})
