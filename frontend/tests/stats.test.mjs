// Run with `npm test` (Node 22's built-in test runner; it reads the TypeScript file directly).
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { median } from '../src/stats.ts'

test('median of an odd count is the middle value', () => {
  assert.equal(median([53.9, 84.2, 70]), 70)
})

test('median of an even count averages the two middle values', () => {
  // regression: the Reactions page showed 84% for yields of 84.2% and 53.9%
  assert.equal(median([84.2, 53.9]), 69.05)
  assert.equal(median([10, 40, 20, 30]), 25)
})

test('median ignores order, and has no value for an empty list', () => {
  assert.equal(median([3, 1, 2]), 2)
  assert.equal(median([]), null)
  assert.equal(median([NaN, 5]), 5)
})
