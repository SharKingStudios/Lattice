import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { api, type Arrival, type Route, type Stop, type Vehicle } from './api'
import { TransitMap } from './transit-map'
import { minutesUntil, rangeUntil } from './time'

export function App() {
  if (window.location.pathname === '/diagnostics') return <Diagnostics />
  return <RiderApp />
}

function RiderApp() {
  const [selectedRoute, setSelectedRoute] = useState<string>()
  const [selectedStop, setSelectedStop] = useState<Stop>()
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle>()
  const [search, setSearch] = useState('')
  const routesQuery = useQuery({ queryKey: ['routes'], queryFn: api.routes })
  const stopsQuery = useQuery({ queryKey: ['stops'], queryFn: () => api.stops() })
  const vehiclesQuery = useQuery({ queryKey: ['vehicles'], queryFn: api.vehicles, refetchInterval: 12_000 })
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000 })
  const routes = routesQuery.data?.routes || []
  const routeDetails = useQueries({ queries: routes.map((route) => ({ queryKey: ['route', route.id], queryFn: () => api.route(route.id), staleTime: 5 * 60_000, enabled: Boolean(routes.length) })) })
  const shapes = routeDetails.flatMap((query, index) => query.data?.shapes.map((shape) => ({ routeId: routes[index].id, color: routes[index].color, coordinates: shape.coordinates, shapeId: shape.shape_id })) || [])
  const stops = stopsQuery.data?.stops || []
  const vehicles = vehiclesQuery.data?.vehicles || []
  const stopInfo = useQuery({ queryKey: ['stop', selectedStop?.id], queryFn: () => api.stop(selectedStop!.id), enabled: Boolean(selectedStop), refetchInterval: 12_000 })
  const lastStreamEvent = useSseRefresh()
  useEffect(() => { if (lastStreamEvent) void vehiclesQuery.refetch() }, [lastStreamEvent])
  const filteredRoutes = useMemo(() => routes.filter((route) => `${route.name} ${route.short_name || ''}`.toLowerCase().includes(search.toLowerCase())), [routes, search])
  const filteredStops = useMemo(() => stops.filter((stop) => stop.name.toLowerCase().includes(search.toLowerCase())).slice(0, 8), [stops, search])
  const stale = vehiclesQuery.data?.stale || healthQuery.data?.status === 'degraded'

  return <main className="app-shell">
    <TransitMap routes={routes} shapes={shapes} stops={stops} vehicles={vehicles} selectedRoute={selectedRoute} selectedStop={selectedStop?.id} onStop={(stop) => { setSelectedStop(stop); setSelectedVehicle(undefined) }} onVehicle={(vehicle) => { setSelectedVehicle(vehicle); setSelectedStop(undefined) }} />
    <header className="topbar"><a className="brand" href="/" aria-label="UGA Bus home"><span className="brand-mark">U</span><span>UGA <em>BUS</em></span></a><label className="search"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a route or stop" aria-label="Find a route or stop" /></label><button className="clear" onClick={() => { setSelectedRoute(undefined); setSelectedStop(undefined); setSelectedVehicle(undefined); setSearch('') }}>Clear</button></header>
    {stale && <div className="stale-banner" role="status">Live information is delayed — showing the last confirmed positions.</div>}
    {search && <section className="search-results" aria-label="Search results"><p>Routes</p>{filteredRoutes.slice(0, 5).map((route) => <button key={route.id} onClick={() => { setSelectedRoute(route.id); setSearch('') }}><i style={{ background: route.color }} />{route.name}</button>)}<p>Stops</p>{filteredStops.map((stop) => <button key={stop.id} onClick={() => { setSelectedStop(stop); setSearch('') }}><span className="stop-symbol">●</span>{stop.name}</button>)}</section>}
    <section className="route-rail" aria-label="Routes"><button className={!selectedRoute ? 'route-chip selected' : 'route-chip'} onClick={() => setSelectedRoute(undefined)}>All routes</button>{routes.filter((route) => route.active !== false).map((route) => <button key={route.id} className={selectedRoute === route.id ? 'route-chip selected' : 'route-chip'} style={{ '--route': route.color } as CSSProperties} onClick={() => setSelectedRoute(selectedRoute === route.id ? undefined : route.id)}><i />{route.short_name || route.name}</button>)}</section>
    <aside className="map-attribution">Map © <a href="https://openfreemap.org/" target="_blank">OpenFreeMap</a> · Transit data © UGA / Passio</aside>
    {selectedStop && <StopSheet stop={selectedStop} arrivals={stopInfo.data?.arrivals || []} loading={stopInfo.isLoading} onClose={() => setSelectedStop(undefined)} />}
    {selectedVehicle && <VehicleSheet vehicle={selectedVehicle} route={routes.find((route) => route.id === selectedVehicle.route_id)} onClose={() => setSelectedVehicle(undefined)} />}
    {routesQuery.isError && <div className="offline-card">Couldn’t load transit data. We’ll keep trying.</div>}
  </main>
}

function StopSheet({ stop, arrivals, loading, onClose }: { stop: Stop; arrivals: Arrival[]; loading: boolean; onClose: () => void }) {
  return <section className="detail-sheet"><button className="sheet-close" onClick={onClose} aria-label="Close">×</button><p className="eyebrow">STOP</p><h1>{stop.name}</h1><p className="muted">{stop.routes.length ? `Served by ${stop.routes.join(', ')}` : 'Loading route details'}</p><div className="arrivals"><h2>Approaching</h2>{loading ? <p className="muted">Checking live arrivals…</p> : arrivals.length ? arrivals.map((arrival, index) => <article className="arrival" key={`${arrival.trip_id}-${index}`}><i style={{ background: arrival.route?.color || '#ba0c2f' }} /><div><strong>{arrival.route?.name || arrival.route_id || 'Campus Transit'}</strong><small>{arrival.vehicle_id ? `Bus ${arrival.vehicle_id}` : 'Vehicle not identified'} · {arrival.data_age_seconds ? `updated ${Math.round(arrival.data_age_seconds)}s ago` : 'live'}</small></div><b>{rangeUntil(arrival.eta_range) || minutesUntil(arrival.our_eta || arrival.passio_eta)}</b></article>) : <p className="muted">No live arrivals are currently published for this stop.</p>}</div></section>
}

function VehicleSheet({ vehicle, route, onClose }: { vehicle: Vehicle; route?: Route; onClose: () => void }) {
  return <section className="detail-sheet compact"><button className="sheet-close" onClick={onClose} aria-label="Close">×</button><p className="eyebrow">LIVE BUS</p><h1>{route?.name || vehicle.route_id || 'Campus Transit'}</h1><p className="muted">Bus {vehicle.vehicle_id || vehicle.id}{vehicle.next_stop_id ? ` · Next: ${vehicle.next_stop_id}` : ''}</p><div className="confidence">{vehicle.match_confidence && vehicle.match_confidence >= .65 ? 'Position matched to route' : 'Position is estimated from the live feed'}</div></section>
}

function useSseRefresh() {
  const [lastEvent, setLastEvent] = useState(0)
  useEffect(() => { const stream = new EventSource('/api/v1/stream'); stream.addEventListener('vehicles', () => setLastEvent(Date.now())); return () => stream.close() }, [])
  return lastEvent
}

function Diagnostics() {
  const query = useQuery({ queryKey: ['diagnostics'], queryFn: api.diagnostics, refetchInterval: 10_000 })
  return <main className="diagnostics"><a href="/">← Rider map</a><p className="eyebrow">PIPELINE DIAGNOSTICS</p><h1>UGA Bus collector</h1>{query.isLoading && <p>Loading diagnostics…</p>}{query.isError && <p>This diagnostics endpoint is protected or unavailable.</p>}{query.data && <pre>{JSON.stringify(query.data, null, 2)}</pre>}</main>
}
