import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

// findBy*/waitFor give up after 1 s by default, too short on a busy CI runner.
configure({ asyncUtilTimeout: 5_000 })

// Tests never call the real API. Locally VITE_API_URL comes from secrets/.env, so a test
// that renders a staff dashboard without mocking its loader (or src/lib/api.ts) would
// otherwise send a real request. Blocked here, it fails as a network error instead.
// Tests that need fetch stub it (vi.stubGlobal / vi.spyOn), which restores this after.
globalThis.fetch = () =>
  Promise.reject(new TypeError('fetch is blocked in tests: mock src/lib/api.ts or stub fetch'))

afterEach(() => {
  cleanup()
})
