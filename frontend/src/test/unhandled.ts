/**
 * Collects unhandled promise rejections (Node process level, which is where they surface
 * under Vitest) so a test can assert there were none.
 */
interface ProcessLike {
  on: (event: 'unhandledRejection', listener: (reason: unknown) => void) => void
  off: (event: 'unhandledRejection', listener: (reason: unknown) => void) => void
}

export function trackUnhandledRejections(): { reasons: unknown[]; stop: () => void } {
  const proc = (globalThis as unknown as { process: ProcessLike }).process
  const reasons: unknown[] = []
  const listener = (reason: unknown) => {
    reasons.push(reason)
  }
  proc.on('unhandledRejection', listener)
  return { reasons, stop: () => proc.off('unhandledRejection', listener) }
}

/** Lets pending microtasks and a macrotask run so rejections get reported. */
export function flushPromises(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}
