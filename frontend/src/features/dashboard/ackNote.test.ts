import {
  ACK_NOTE_COUNT_FROM,
  ACK_NOTE_ERRORS,
  ACK_NOTE_MAX,
  RESOLVE_NOTE_ERRORS,
  checkAckNote,
  checkResolveNote,
  normalizeAckNote,
} from './ackNote'

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
    ['a zero-width space', 'Synthetic\u200Bnote'],
    ['a right-to-left override', 'Synthetic \u202Enote'],
    ['a line separator', 'Synthetic\u2028note'],
    ['a paragraph separator', 'Synthetic\u2029note'],
    ['a bare carriage return', 'Synthetic\rnote'],
    ['a NUL', 'Synthetic\u0000note'],
    ['a zero-width joiner', 'Synthetic\u200Dnote'],
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

// The staff resolve's closing note (docs/data-model.md, Track B route: resolve): the
// same rules as the acknowledge note, but required. Cases follow the backend's
// `valid_note` (lambdas/api-alert-actions/src/rules.py), which also refuses Zl/Zp.
describe('checkResolveNote', () => {
  it.each([[''], ['   '], ['\n\n'], [' \r\n '], ['\t'], ['　']])('%j is refused as empty', (raw) => {
    expect(checkResolveNote(raw)).toEqual({ ok: false, error: 'empty' })
  })

  it('accepts a note, normalised: CRLF to LF, then trimmed', () => {
    expect(checkResolveNote('  Synthetic note: resident checked.\r\nFalse alarm.\r\n ')).toEqual({
      ok: true,
      note: 'Synthetic note: resident checked.\nFalse alarm.',
    })
  })

  it('keeps an inner newline (the one allowed control character)', () => {
    expect(checkResolveNote('Line one\nLine two')).toEqual({ ok: true, note: 'Line one\nLine two' })
  })

  it.each([
    ['a tab', 'Synthetic\tnote'],
    ['a zero-width space', 'Synthetic\u200Bnote'],
    ['a zero-width joiner', 'Synthetic\u200Dnote'],
    ['a zero-width no-break space inside', 'Synthetic\uFEFFnote'],
    ['a right-to-left override', 'Synthetic \u202Enote'],
    ['a line separator (U+2028, category Zl)', 'Synthetic\u2028note'],
    ['a paragraph separator (U+2029, category Zp)', 'Synthetic\u2029note'],
    ['a bare carriage return', 'Synthetic\rnote'],
    ['a NUL', 'Synthetic\u0000note'],
    ['a DEL', 'Synthetic\u007fnote'],
    ['a C1 control (NEL)', 'Synthetic\u0085note'],
    ['an unassigned code point (category Cn)', 'Synthetic\u0378note'],
  ])('refuses %s as hidden', (_, raw) => {
    expect(checkResolveNote(raw)).toEqual({ ok: false, error: 'hidden' })
  })

  it('a note of only hidden characters is hidden, not empty (they are not whitespace)', () => {
    expect(checkResolveNote('\u200B')).toEqual({ ok: false, error: 'hidden' })
  })

  it('accepts exactly 500 characters and refuses 501', () => {
    expect(checkResolveNote('a'.repeat(ACK_NOTE_MAX))).toEqual({ ok: true, note: 'a'.repeat(500) })
    expect(checkResolveNote('a'.repeat(ACK_NOTE_MAX + 1))).toEqual({ ok: false, error: 'tooLong' })
  })

  it('counts code points, not UTF-16 units: 500 emoji pass, 501 do not', () => {
    const emoji500 = '\u{1F691}'.repeat(500)
    expect(emoji500.length).toBe(1000)
    expect(checkResolveNote(emoji500)).toEqual({ ok: true, note: emoji500 })
    expect(checkResolveNote('\u{1F691}'.repeat(501))).toEqual({ ok: false, error: 'tooLong' })
  })

  it('counts the length after normalising, so 500 characters with padding pass', () => {
    expect(checkResolveNote(`  ${'a'.repeat(500)}\r\n`)).toEqual({ ok: true, note: 'a'.repeat(500) })
  })

  it('has the acknowledge messages plus one for an empty note', () => {
    expect(RESOLVE_NOTE_ERRORS).toEqual({
      hidden: 'Remove hidden or control characters from the note.',
      tooLong: 'Keep the note to 500 characters or fewer.',
      empty: 'Add a note saying how this alert was dealt with.',
    })
  })

  it('leaves checkAckNote unchanged: empty is still no note there', () => {
    expect(checkAckNote('   ')).toEqual({ ok: true, note: undefined })
    expect(checkAckNote('Synthetic\u2028note')).toEqual({ ok: false, error: 'hidden' })
  })
})
