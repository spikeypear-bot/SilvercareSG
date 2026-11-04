import { useCallback, useState } from 'react'

const NO_BREAK_SPACE = String.fromCharCode(0xa0)

/**
 * Text for a page's one screen-reader announcer (`AlertAnnouncer`). `announce` replaces
 * the message. Repeating the same text still changes the region, so screen readers read
 * it again.
 */
export function useAnnouncer(): { message: string; announce: (message: string) => void } {
  // `count` alternates a trailing no-break space.
  const [{ text, count }, setState] = useState({ text: '', count: 0 })
  const announce = useCallback((next: string) => {
    setState((s) => ({ text: next, count: s.count + 1 }))
  }, [])
  return { message: count % 2 ? text + NO_BREAK_SPACE : text, announce }
}
