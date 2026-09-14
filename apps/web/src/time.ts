export function minutesUntil(value?: string | null): string {
  if (!value) return 'No estimate'
  const minutes = Math.max(0, Math.round((new Date(value).getTime() - Date.now()) / 60_000))
  return minutes < 1 ? 'Due' : `${minutes} min`
}

export function rangeUntil(range?: [string, string] | null): string | null {
  if (!range) return null
  const a = Math.max(0, Math.round((new Date(range[0]).getTime() - Date.now()) / 60_000))
  const b = Math.max(0, Math.round((new Date(range[1]).getTime() - Date.now()) / 60_000))
  return a === b ? (a < 1 ? 'Due' : `${a} min`) : `${a}–${b} min`
}
