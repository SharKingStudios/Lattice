import { describe, expect, it } from 'vitest'
import { minutesUntil, rangeUntil } from './time'

describe('rider ETA text', () => {
  it('renders a usable range', () => {
    const now = Date.now()
    expect(rangeUntil([new Date(now + 3 * 60_000).toISOString(), new Date(now + 5 * 60_000).toISOString()])).toBe('3–5 min')
  })

  it('does not show a negative wait', () => {
    expect(minutesUntil(new Date(Date.now() - 60_000).toISOString())).toBe('Due')
  })
})
