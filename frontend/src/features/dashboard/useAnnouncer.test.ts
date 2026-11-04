import { act, renderHook } from '@testing-library/react'
import { useAnnouncer } from './useAnnouncer'

const NBSP = ' '

describe('useAnnouncer', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useAnnouncer())
    expect(result.current.message).toBe('')
  })

  it('announce() replaces the message', () => {
    const { result } = renderHook(() => useAnnouncer())
    act(() => result.current.announce('First message.'))
    expect(result.current.message.replace(/ $/, '')).toBe('First message.')
    act(() => result.current.announce('Second message.'))
    expect(result.current.message.replace(/ $/, '')).toBe('Second message.')
  })

  it('repeating the same text still changes the message, so screen readers read it again', () => {
    const { result } = renderHook(() => useAnnouncer())
    const text = 'Showing 6 of 40 devices: offline.'
    const seen: string[] = []
    for (let n = 0; n < 4; n++) {
      act(() => result.current.announce(text))
      seen.push(result.current.message)
    }
    // Each message differs from the one before, only by a trailing no-break space.
    seen.slice(1).forEach((m, i) => expect(m).not.toBe(seen[i]))
    expect(seen.map((m) => m.replace(/ $/, ''))).toEqual([text, text, text, text])
    expect(new Set(seen)).toEqual(new Set([text, `${text}${NBSP}`]))
  })

  it('keeps the same announce function between renders', () => {
    const { result, rerender } = renderHook(() => useAnnouncer())
    const first = result.current.announce
    act(() => result.current.announce('Hello.'))
    rerender()
    expect(result.current.announce).toBe(first)
  })
})
