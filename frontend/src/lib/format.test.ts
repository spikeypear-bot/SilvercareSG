import { dayPart, formatRelativeTime, initials } from './format'

describe('formatRelativeTime', () => {
  const now = new Date('2026-10-18T03:00:00Z')
  const ago = (minutes: number) => new Date(now.getTime() - minutes * 60_000).toISOString()

  it.each([
    [0, 'just now'],
    [12, '12 min ago'],
    [59, '59 min ago'],
    [60, '1 h ago'],
    [180, '3 h ago'],
    [24 * 60, '1 day ago'],
    [3 * 24 * 60, '3 days ago'],
  ])('%i minutes -> %s', (minutes, text) => {
    expect(formatRelativeTime(ago(minutes), now)).toBe(text)
  })
})

describe('dayPart uses Singapore time (UTC+8)', () => {
  it.each([
    ['2026-10-18T23:30:00Z', 'morning'], // 07:30 SGT
    ['2026-10-18T03:59:00Z', 'morning'], // 11:59 SGT
    ['2026-10-18T04:00:00Z', 'afternoon'], // 12:00 SGT
    ['2026-10-18T10:00:00Z', 'evening'], // 18:00 SGT
  ])('%s -> %s', (iso, part) => {
    expect(dayPart(new Date(iso))).toBe(part)
  })
})

describe('initials', () => {
  it.each([
    ['Test Resident A', 'TA'],
    ['test', 'T'],
    ['   ', '?'],
  ])('%j -> %s', (name, expected) => {
    expect(initials(name)).toBe(expected)
  })
})
