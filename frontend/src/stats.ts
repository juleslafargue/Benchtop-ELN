/** Small number helpers shared by the pages. No imports, so `node --test` can run their tests directly. */

/** The middle value; for an even count, the average of the two middle values. null when there are none. */
export function median(values: number[]): number | null {
  const v = values.filter(x => Number.isFinite(x)).sort((a, b) => a - b)
  if (!v.length) return null
  const mid = Math.floor(v.length / 2)
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2
}
