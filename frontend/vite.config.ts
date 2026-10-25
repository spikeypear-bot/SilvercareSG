/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The local .env lives in the repo's git-ignored secrets/ folder. CI sets the VITE_*
  // values as environment variables instead, which Vite also reads.
  envDir: '../secrets',
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // CI runners are slower than dev machines; the defaults (5 s) made tests flaky there.
    testTimeout: 15_000,
  },
})
