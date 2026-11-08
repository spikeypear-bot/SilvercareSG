import { useEffect, useSyncExternalStore } from 'react'

/**
 * Pauses background refreshes (30-second polling, refetch on focus, refetch after an
 * action) while a form is open, so a poll never re-renders or removes the row someone is
 * typing a note in. Forms call `useHoldRefresh(true)` while mounted or open; the data
 * hooks read `useRefreshHeld()` and run any refresh they skipped once the hold ends.
 * One count for the whole page: only one page is mounted at a time.
 */
let holds = 0
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function isHeld() {
  return holds > 0
}

/** True while any form holds refreshes. */
export function useRefreshHeld(): boolean {
  return useSyncExternalStore(subscribe, isHeld, isHeld)
}

/** Holds refreshes while `active` is true (and the calling component is mounted). */
export function useHoldRefresh(active = true): void {
  useEffect(() => {
    if (!active) return
    holds += 1
    emit()
    return () => {
      holds -= 1
      emit()
    }
  }, [active])
}
