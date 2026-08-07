import { defineConfig } from 'vitest/config'

// Tests cover the pure graph/search/lineage modules (plain .ts, no DOM),
// so the node environment is enough -- no jsdom dependency.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
