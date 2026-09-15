export type Route = { id: string; short_name: string | null; long_name: string | null; name: string; color: string; text_color: string; active?: boolean }
export type Stop = { id: string; name: string; code?: string | null; latitude: number; longitude: number; routes: string[] }
export type Vehicle = { id: string; vehicle_id?: string | null; trip_id?: string | null; route_id?: string | null; latitude: number; longitude: number; bearing?: number | null; speed?: number | null; next_stop_id?: string | null; matched_shape_id?: string | null; match_confidence?: number | null; match_method?: string | null }
export type Arrival = { route_id?: string | null; route?: Route | null; vehicle_id?: string | null; trip_id?: string | null; stop_id?: string | null; stop_sequence?: number | null; our_eta?: string | null; eta_range?: [string, string] | null; confidence: number; prediction_source: string; passio_eta?: string | null; data_age_seconds?: number | null }
export type RouteDetail = { route: Route; shapes: { shape_id: string; direction_ids: number[]; coordinates: [number, number][] }[]; stops: Stop[]; vehicles: Vehicle[]; arrivals?: Arrival[] }
export type Health = { status: string; upstream: Record<string, { age_seconds?: number | null; fresh: boolean; last_error?: string | null }> }

async function fetchApi<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return response.json() as Promise<T>
}

export const api = {
  routes: () => fetchApi<{ routes: Route[] }>('/routes'),
  route: (id: string) => fetchApi<RouteDetail>(`/routes/${encodeURIComponent(id)}`),
  stops: (q?: string) => fetchApi<{ stops: Stop[] }>(`/stops${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  stop: (id: string) => fetchApi<{ stop: Stop; arrivals: Arrival[]; data_fresh: boolean }>(`/stops/${encodeURIComponent(id)}`),
  vehicles: () => fetchApi<{ vehicles: Vehicle[]; stale: boolean; data_age_seconds?: number | null }>('/vehicles'),
  health: () => fetchApi<Health>('/health'),
  diagnostics: () => fetchApi<Record<string, unknown>>('/diagnostics'),
}
