import { useCallback, useEffect, useState } from 'react'
import { getNightlyOverview } from '../../lib/api'
import type { NightlyOverviewResponse } from '../../types/nightly'

export type LastNightState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'loaded'; data: NightlyOverviewResponse }

/**
 * Loads `GET /nightly/overview` once when the doctor dashboard opens, apart from the
 * dashboard's 30 s polling (docs/features/nightly-overview.md, criterion 18). The API
 * client reuses the tab's copy for 10 minutes; `retry` ("Try again") skips it and reloads
 * only this section.
 */
export function useLastNight() {
  const [state, setState] = useState<LastNightState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    getNightlyOverview({ fresh: attempt > 0 })
      .then((data) => {
        if (!cancelled) setState({ status: 'loaded', data })
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'error' })
      })
    return () => {
      cancelled = true
    }
  }, [attempt])

  const retry = useCallback(() => {
    setState({ status: 'loading' })
    setAttempt((a) => a + 1)
  }, [])

  return { state, retry }
}
