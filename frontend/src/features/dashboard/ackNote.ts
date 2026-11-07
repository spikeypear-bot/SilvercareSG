/**
 * The nurse's acknowledge note (`ack_note`) and the staff resolve's closing note
 * (`resolve_note`, required; `checkResolveNote` below), with main's `note` rules
 * (docs/data-model.md, Status updates; docs/features/alert-actions-ui.md, criteria 3-6):
 * `\r\n` becomes `\n`, then the note is trimmed; empty means no note; otherwise 1-500
 * characters, with no control or other non-printing character (Unicode category C)
 * except a newline. The API checks the same rules, so a note the form accepts is never
 * refused there.
 */

export const ACK_NOTE_MAX = 500

/** The form shows "N of 500 characters" from this length on. */
export const ACK_NOTE_COUNT_FROM = 400

/**
 * Any Unicode category C character (control, format, ...) except a newline, and the line
 * and paragraph separators U+2028/U+2029 (categories Zl, Zp): exactly what
 * `alert-processor` refuses (`_valid_note` in lambdas/alert-processor/src/message.py).
 */
const HIDDEN_CHARACTER = /[^\P{C}\n]|[\p{Zl}\p{Zp}]/u

export type AckNoteError = 'hidden' | 'tooLong'

export type AckNoteResult = { ok: true; note: string | undefined } | { ok: false; error: AckNoteError }

export const ACK_NOTE_ERRORS: Record<AckNoteError, string> = {
  hidden: 'Remove hidden or control characters from the note.',
  tooLong: `Keep the note to ${ACK_NOTE_MAX} characters or fewer.`,
}

/** `\r\n` to `\n`, then trimmed at both ends. */
export function normalizeAckNote(raw: string): string {
  return raw.replace(/\r\n/g, '\n').trim()
}

/** Why a non-empty normalised note can't be sent, or null if it can. */
function noteProblem(note: string): AckNoteError | null {
  if (HIDDEN_CHARACTER.test(note)) return 'hidden'
  // Characters as the API counts them (code points), not UTF-16 units.
  if ([...note].length > ACK_NOTE_MAX) return 'tooLong'
  return null
}

/** The note to send (`undefined` for none), or why it can't be sent. */
export function checkAckNote(raw: string): AckNoteResult {
  const note = normalizeAckNote(raw)
  if (note === '') return { ok: true, note: undefined }
  const error = noteProblem(note)
  return error ? { ok: false, error } : { ok: true, note }
}

/**
 * The closing note of a staff resolve (`resolve_note`, docs/data-model.md, Track B route:
 * resolve): the same rules as the acknowledge note, but **required**, so empty (or only
 * whitespace) is refused. The API refuses a missing note with 400.
 */
export type ResolveNoteError = AckNoteError | 'empty'

export type ResolveNoteResult = { ok: true; note: string } | { ok: false; error: ResolveNoteError }

export const RESOLVE_NOTE_ERRORS: Record<ResolveNoteError, string> = {
  ...ACK_NOTE_ERRORS,
  empty: 'Add a note saying how this alert was dealt with.',
}

/** The closing note to send, or why it can't be sent. */
export function checkResolveNote(raw: string): ResolveNoteResult {
  const note = normalizeAckNote(raw)
  if (note === '') return { ok: false, error: 'empty' }
  const error = noteProblem(note)
  return error ? { ok: false, error } : { ok: true, note }
}
