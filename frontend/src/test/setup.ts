import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

// findBy*/waitFor give up after 1 s by default, too short on a busy CI runner.
configure({ asyncUtilTimeout: 5_000 })

afterEach(() => {
  cleanup()
})
