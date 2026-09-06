import { defineConfig } from 'vitest/config';

// The fixtures carry UTC timestamps and several assertions are on the clock
// labels the page renders, which are local. Pinning the suite to UTC keeps
// those assertions readable instead of computing the expectation twice.
process.env.TZ = 'UTC';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
    env: { TZ: 'UTC' },
    reporters: ['default'],
  },
});
