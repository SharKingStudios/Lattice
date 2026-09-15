import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { api, type Arrival, type Route, type RouteDetail, type Stop, type Vehicle } from './api'
import { TransitMap } from './transit-map'
import { minutesUntil } from './time'

type Tab = 'map' | 'stops' | 'routes'
type Coordinates = { latitude: number; longitude: number }
type LocationState = { coordinates?: Coordinates; error?: string; request: () => void }
const NEARBY_ROUTE_FEET = 5_500
const RECENT_STOPS_KEY = 'uga-bus-recent-stops'

export function App() {
  if (window.location.pathname === '/diagnostics') return <Diagnostics />
  return <RiderApp />
}

function RiderApp() {
  const [activeTab, setActiveTab] = useState<Tab>('map')
  const [sheetExpanded, setSheetExpanded] = useState(false)
  const [selectedRoute, setSelectedRoute] = useState<string>()
  const [selectedStop, setSelectedStop] = useState<Stop>()
  const [selectedStopRoute, setSelectedStopRoute] = useState<string>()
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle>()
  const [search, setSearch] = useState('')
  const [recentStopIds, setRecentStopIds] = useState<string[]>(readRecentStops)
  const location = useUserLocation()
  const routesQuery = useQuery({ queryKey: ['routes'], queryFn: api.routes })
  const stopsQuery = useQuery({ queryKey: ['stops'], queryFn: () => api.stops() })
  const vehiclesQuery = useQuery({ queryKey: ['vehicles'], queryFn: api.vehicles, refetchInterval: 12_000 })
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15_000 })
  const routes = routesQuery.data?.routes || []
  const stops = stopsQuery.data?.stops || []
  const vehicles = vehiclesQuery.data?.vehicles || []
  const routeDetails = useQueries({
    queries: routes.map((route) => ({
      queryKey: ['route', route.id],
      queryFn: () => api.route(route.id),
      staleTime: 5 * 60_000,
      enabled: Boolean(routes.length),
    })),
  })
  const routeDetailsById = useMemo(() => new Map<string, RouteDetail | undefined>(
    routes.map((route, index) => [route.id, routeDetails[index]?.data]),
  ), [routes, routeDetails])
  const shapes = useMemo(() => routeDetails.flatMap((query, index) =>
    query.data?.shapes.map((shape) => ({
      routeId: routes[index].id,
      color: routes[index].color,
      coordinates: shape.coordinates,
      shapeId: shape.shape_id,
    })) || []), [routeDetails, routes])
  const liveRouteIds = useMemo(() => new Set(vehicles.map((vehicle) => vehicle.route_id).filter(Boolean) as string[]), [vehicles])
  const routeDistances = useMemo(() => distanceByRoute(routes, stops, location.coordinates), [routes, stops, location.coordinates])
  const stopInfo = useQuery({
    queryKey: ['stop', selectedStop?.id],
    queryFn: () => api.stop(selectedStop!.id),
    enabled: Boolean(selectedStop),
    refetchInterval: 12_000,
  })
  const selectedRouteLiveQuery = useQuery({
    queryKey: ['route-live', selectedStopRoute],
    queryFn: () => api.route(selectedStopRoute!),
    enabled: Boolean(selectedStop && selectedStopRoute),
    refetchInterval: 12_000,
    staleTime: 0,
  })
  const selectedRouteQuery = useQuery({
    queryKey: ['selected-route', selectedRoute],
    queryFn: () => api.route(selectedRoute!),
    enabled: Boolean(selectedRoute),
    refetchInterval: 12_000,
    staleTime: 0,
  })
  const lastStreamEvent = useSseRefresh()
  useEffect(() => { if (lastStreamEvent) void vehiclesQuery.refetch() }, [lastStreamEvent, vehiclesQuery])
  useEffect(() => {
    try { localStorage.setItem(RECENT_STOPS_KEY, JSON.stringify(recentStopIds)) } catch { /* Storage is optional. */ }
  }, [recentStopIds])

  const chooseMainTab = (tab: Tab) => {
    setActiveTab(tab)
    setSheetExpanded(tab !== 'map')
    setSelectedVehicle(undefined)
    setSelectedRoute(undefined)
    setSelectedStop(undefined)
    setSelectedStopRoute(undefined)
  }
  const chooseStop = useCallback((stop: Stop) => {
    setSelectedStop(stop)
    setSelectedStopRoute(undefined)
    setSelectedVehicle(undefined)
    setRecentStopIds((current) => [stop.id, ...current.filter((id) => id !== stop.id)].slice(0, 6))
    setActiveTab('stops')
    setSheetExpanded(true)
  }, [])
  const chooseRoute = useCallback((routeId: string) => {
    if (!liveRouteIds.has(routeId)) return
    setSelectedRoute(routeId)
    setSelectedStop(undefined)
    setSelectedStopRoute(undefined)
    setSelectedVehicle(undefined)
    setActiveTab('routes')
    setSheetExpanded(true)
  }, [liveRouteIds])
  const filteredRoutes = useMemo(() => routes.filter((route) => routeText(route).includes(search.toLowerCase())), [routes, search])
  const filteredStops = useMemo(() => stops.filter((stop) => stop.name.toLowerCase().includes(search.toLowerCase())).slice(0, 8), [stops, search])
  const stale = vehiclesQuery.data?.stale || healthQuery.data?.status === 'degraded'
  const selectedRouteRecord = routes.find((route) => route.id === selectedRoute)
  const nextStopName = selectedVehicle?.next_stop_id
    ? stops.find((stop) => stop.id === selectedVehicle.next_stop_id)?.name
      || (selectedVehicle.route_id ? routeDetailsById.get(selectedVehicle.route_id)?.stops.find((stop) => stop.id === selectedVehicle.next_stop_id)?.name : undefined)
    : undefined
  const sheetDrag = useSheetDrag(sheetExpanded, setSheetExpanded)

  return <main className="app-shell">
    <TransitMap
      routes={routes}
      shapes={shapes}
      stops={stops}
      vehicles={vehicles}
      activeRouteIds={liveRouteIds}
      selectedRoute={selectedRoute}
      selectedStop={selectedStop?.id}
      onStop={chooseStop}
      onVehicle={(vehicle) => { setSelectedVehicle(vehicle); setSelectedStop(undefined); setSelectedStopRoute(undefined); setSelectedRoute(vehicle.route_id || undefined); setActiveTab(vehicle.route_id ? 'routes' : 'map'); setSheetExpanded(true) }}
    />
    <header className="topbar">
      <a className="brand" href="/" aria-label="UGA Bus home"><span className="brand-mark">U</span><span>UGA <em>BUS</em></span></a>
      <label className="search">
        <span aria-hidden="true">⌕</span>
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a route or stop" aria-label="Find a route or stop" />
        {search && <button className="search-reset" onClick={() => setSearch('')} aria-label="Clear search">×</button>}
      </label>
    </header>
    {stale && <div className="stale-banner" role="status">Live information is delayed — showing the last confirmed positions.</div>}
    {search && <section className="search-results" aria-label="Search results">
      <p>ROUTES</p>
      {filteredRoutes.slice(0, 5).map((route) => <button key={route.id} disabled={!liveRouteIds.has(route.id)} onClick={() => { chooseRoute(route.id); setSearch('') }}><i style={{ background: route.color }} />{route.name}{!liveRouteIds.has(route.id) && <small>No buses now</small>}</button>)}
      <p>STOPS</p>
      {filteredStops.map((stop) => <button key={stop.id} onClick={() => { chooseStop(stop); setSearch('') }}><span className="stop-symbol">●</span>{stop.name}</button>)}
    </section>}
    <aside className="map-attribution">Map © <a href="https://openfreemap.org/" target="_blank" rel="noreferrer">OpenFreeMap</a> · Transit data © UGA / Passio</aside>

    <div className="bottom-controls">
      <section className={`bottom-sheet ${sheetExpanded ? 'is-expanded' : ''} ${sheetDrag.isDragging ? 'is-dragging' : ''}`} style={sheetDrag.style} aria-label="Transit controls">
        <button className="sheet-grabber" onClick={sheetDrag.onClick} onPointerDown={sheetDrag.onPointerDown} onPointerMove={sheetDrag.onPointerMove} onPointerUp={sheetDrag.onPointerUp} onPointerCancel={sheetDrag.onPointerCancel} aria-label={sheetExpanded ? 'Minimize panel' : 'Expand panel'}><span /></button>
        <div className="sheet-content">
          {activeTab === 'map' && <MapPanel busCount={vehicles.length} selectedRoute={selectedRouteRecord} selectedVehicle={selectedVehicle} nextStopName={nextStopName} onClearRoute={() => setSelectedRoute(undefined)} />}
          {activeTab === 'stops' && <StopsPanel stops={stops} routes={routes} vehicles={vehicles} selectedStop={selectedStop} selectedStopRoute={selectedStopRoute} routeDetails={routeDetailsById} arrivals={stopInfo.data?.arrivals || []} routeArrivals={selectedRouteLiveQuery.data?.arrivals || []} loadingArrivals={stopInfo.isLoading || selectedRouteLiveQuery.isFetching} location={location} recentStopIds={recentStopIds} activeRouteIds={liveRouteIds} onStop={chooseStop} onCloseStop={() => { setSelectedStop(undefined); setSelectedStopRoute(undefined) }} onRoute={(routeId) => { setSelectedStopRoute(routeId); setSelectedRoute(routeId) }} />}
          {activeTab === 'routes' && <RoutesPanel routes={routes} routeDistances={routeDistances} liveRouteIds={liveRouteIds} selectedRoute={selectedRoute} routeDetail={selectedRouteQuery.data || (selectedRoute ? routeDetailsById.get(selectedRoute) : undefined)} selectedVehicle={selectedVehicle} location={location} onStop={chooseStop} onRoute={chooseRoute} onShowAll={() => { setSelectedRoute(undefined); setSelectedVehicle(undefined) }} />}
        </div>
      </section>
      <nav className="bottom-nav" aria-label="Main navigation">
        <button className={activeTab === 'map' ? 'is-active' : ''} onClick={() => chooseMainTab('map')}><span aria-hidden="true">⌖</span>Map</button>
        <button className={activeTab === 'stops' ? 'is-active' : ''} onClick={() => chooseMainTab('stops')}><span aria-hidden="true">●</span>Stops</button>
        <button className={activeTab === 'routes' ? 'is-active' : ''} onClick={() => chooseMainTab('routes')}><span aria-hidden="true">≋</span>Routes</button>
      </nav>
    </div>
    {routesQuery.isError && <div className="offline-card">Couldn’t load transit data. We’ll keep trying.</div>}
  </main>
}

function MapPanel({ busCount, selectedRoute, selectedVehicle, nextStopName, onClearRoute }: { busCount: number; selectedRoute?: Route; selectedVehicle?: Vehicle; nextStopName?: string; onClearRoute: () => void }) {
  return <div className="map-panel"><p className="eyebrow">MAP</p>{selectedRoute ? <div className="panel-title-row"><h1>{selectedRoute.name}</h1><button className="text-button" onClick={onClearRoute}>All routes</button></div> : <h1>{busCount ? `${busCount} ${busCount === 1 ? 'bus' : 'buses'} running` : 'No buses running'}</h1>}{selectedVehicle && <p className="vehicle-note">Bus {selectedVehicle.vehicle_id || selectedVehicle.id}{nextStopName ? ` · Next: ${nextStopName}` : ''}</p>}</div>
}

function StopsPanel({ stops, routes, vehicles, selectedStop, selectedStopRoute, routeDetails, arrivals, routeArrivals, loadingArrivals, location, recentStopIds, activeRouteIds, onStop, onCloseStop, onRoute }: { stops: Stop[]; routes: Route[]; vehicles: Vehicle[]; selectedStop?: Stop; selectedStopRoute?: string; routeDetails: Map<string, RouteDetail | undefined>; arrivals: Arrival[]; routeArrivals: Arrival[]; loadingArrivals: boolean; location: LocationState; recentStopIds: string[]; activeRouteIds: Set<string>; onStop: (stop: Stop) => void; onCloseStop: () => void; onRoute: (routeId: string) => void }) {
  if (selectedStop) return <StopTimeline stop={selectedStop} routes={routes} vehicles={vehicles} selectedRoute={selectedStopRoute} routeDetails={routeDetails} arrivals={arrivals} routeArrivals={routeArrivals} loading={loadingArrivals} location={location.coordinates} activeRouteIds={activeRouteIds} onClose={onCloseStop} onRoute={onRoute} />
  const nearbyStops = location.coordinates ? [...stops].map((stop) => ({ stop, feet: distanceFeet(location.coordinates!, stop) })).sort((a, b) => a.feet - b.feet).slice(0, 8) : []
  const recentStops = recentStopIds.map((id) => stops.find((stop) => stop.id === id)).filter(Boolean) as Stop[]
  return <div className="panel-scroll stops-panel"><p className="eyebrow">STOPS</p><h1>Nearby stops</h1>{!location.coordinates && <div className="location-card"><div><strong>Find stops around you</strong><p>{location.error || 'Use your location to sort stops by walking distance.'}</p></div><button onClick={location.request}>Use location</button></div>}{nearbyStops.map(({ stop, feet }) => <StopPick key={stop.id} stop={stop} feet={feet} routes={routes} onClick={() => onStop(stop)} />)}{recentStops.length > 0 && <><h2 className="section-heading">Recent stops</h2>{recentStops.map((stop) => <StopPick key={stop.id} stop={stop} feet={location.coordinates ? distanceFeet(location.coordinates, stop) : undefined} routes={routes} onClick={() => onStop(stop)} />)}</>}</div>
}

function StopPick({ stop, feet, routes, onClick }: { stop: Stop; feet?: number; routes: Route[]; onClick: () => void }) {
  const routeNames = stop.routes.map((id) => routes.find((route) => route.id === id)?.short_name || id).slice(0, 4)
  return <button className="stop-pick" onClick={onClick}><span className="stop-pick-dot" /><span><strong>{stop.name}</strong><small>{routeNames.length ? `Routes ${routeNames.join(', ')}` : 'Campus stop'}</small></span>{feet !== undefined && <b>{formatFeet(feet)}</b>}</button>
}

function StopTimeline({ stop, routes, vehicles, selectedRoute, routeDetails, arrivals, routeArrivals, loading, location, activeRouteIds, onClose, onRoute }: { stop: Stop; routes: Route[]; vehicles: Vehicle[]; selectedRoute?: string; routeDetails: Map<string, RouteDetail | undefined>; arrivals: Arrival[]; routeArrivals: Arrival[]; loading: boolean; location?: Coordinates; activeRouteIds: Set<string>; onClose: () => void; onRoute: (routeId: string) => void }) {
  const serviceRoutes = useMemo(() => stop.routes.map((id) => routes.find((route) => route.id === id)).filter((route): route is Route => Boolean(route && activeRouteIds.has(route.id))).map((route) => ({ route, nearestBus: nearestBusFeet(vehicles, route.id, stop) })).sort((a, b) => a.nearestBus - b.nearestBus), [stop, routes, activeRouteIds, vehicles])
  const routeId = selectedRoute && serviceRoutes.some((item) => item.route.id === selectedRoute) ? selectedRoute : undefined
  const route = routes.find((item) => item.id === routeId)
  const routeStops = routeId ? routeDetails.get(routeId)?.stops || [] : []
  const selectedStopIndex = routeStops.findIndex((routeStop) => routeStop.id === stop.id)
  const timelineStops = selectedStopIndex > 2 ? routeStops.slice(selectedStopIndex - 2) : routeStops
  const routeVehicles = vehicles.filter((vehicle) => vehicle.route_id === routeId)
  const selectedStopArrivals = arrivals.filter((arrival) => arrival.route_id === routeId)
  const timelineArrivals = routeArrivals.filter((arrival) => arrival.route_id === routeId)
  return <div className="panel-scroll stop-timeline-panel"><div className="panel-title-row"><div><p className="eyebrow">STOP</p><h1>{stop.name}</h1></div><button className="close-panel" onClick={onClose} aria-label="Back to stops">×</button></div><div className="serving-routes">{serviceRoutes.map(({ route: candidate }) => { const eta = firstArrivalLabel(arrivals.filter((arrival) => arrival.route_id === candidate.id)); return <button key={candidate.id} className={candidate.id === routeId ? 'serving-route is-selected' : 'serving-route'} style={{ '--route-color': candidate.color } as CSSProperties} onClick={() => onRoute(candidate.id)}><i /><span>{candidate.short_name || candidate.name}</span><b>{eta || 'No estimate'}</b></button> })}</div>{loading && <p className="muted">Refreshing live arrivals…</p>}{!loading && !serviceRoutes.length && <p className="empty-state">No buses are currently serving this stop. Try again when a route is live.</p>}{route && timelineStops.length > 0 && <RouteTimeline route={route} stops={timelineStops} allStops={routeStops} selectedStop={stop} vehicles={routeVehicles} arrivals={timelineArrivals.length ? timelineArrivals : selectedStopArrivals} location={location} />}</div>
}

function RouteTimeline({ route, stops, selectedStop, vehicles, arrivals, location, allStops = stops }: { route: Route; stops: Stop[]; selectedStop?: Stop; vehicles: Vehicle[]; arrivals: Arrival[]; location?: Coordinates; allStops?: Stop[] }) {
  const firstVisibleIndex = Math.max(0, allStops.findIndex((stop) => stop.id === stops[0]?.id))
  const busPositions = vehicles.map((vehicle) => {
    const routeIndex = vehicle.next_stop_id ? allStops.findIndex((stop) => stop.id === vehicle.next_stop_id) : -1
    return { vehicle, index: routeIndex - firstVisibleIndex }
  }).filter((item) => item.index >= 0 && item.index < stops.length)
  return <section className="route-timeline" style={{ '--route-color': route.color } as CSSProperties}>
    <div className="timeline-heading"><span>{route.short_name || route.name}</span><small>UGA estimates</small></div>
    <div className="timeline-track" aria-hidden="true">{busPositions.map(({ vehicle, index }) => <span key={vehicle.id} className="timeline-bus" style={{ '--position': `${timelinePosition(index, stops.length)}%` } as CSSProperties}>▸</span>)}</div>
    <div className="timeline-stops">{stops.map((routeStop, index) => {
      const isSelected = routeStop.id === selectedStop?.id
      const arrivalLabels = arrivalLabelsForRoute(arrivals.filter((arrival) => arrival.stop_id === routeStop.id))
      const time = arrivalLabels[0]
      return <article className={isSelected ? 'timeline-stop is-selected' : 'timeline-stop'} key={`${routeStop.id}-${index}`}>
        <span className="timeline-dot" />
        <div><strong>{routeStop.name}</strong><small className="eta">{time || 'Awaiting live timing'}</small>{arrivalLabels.length > 1 && <small className="other-buses">Other buses: {arrivalLabels.slice(1).join(', ')}</small>}</div>
        {location && <b className="stop-distance">{formatFeet(distanceFeet(location, routeStop))}</b>}
      </article>
    })}</div>
  </section>
}

function RoutesPanel({ routes, routeDistances, liveRouteIds, selectedRoute, routeDetail, selectedVehicle, location, onStop, onRoute, onShowAll }: { routes: Route[]; routeDistances: Map<string, number>; liveRouteIds: Set<string>; selectedRoute?: string; routeDetail?: RouteDetail; selectedVehicle?: Vehicle; location: LocationState; onStop: (stop: Stop) => void; onRoute: (routeId: string) => void; onShowAll: () => void }) {
  if (selectedRoute) return <RouteDetailPanel route={routes.find((route) => route.id === selectedRoute)} detail={routeDetail} selectedVehicle={selectedVehicle} location={location} onStop={onStop} onBack={onShowAll} />
  const active = routes.filter((route) => liveRouteIds.has(route.id)).sort((a, b) => (routeDistances.get(a.id) || Infinity) - (routeDistances.get(b.id) || Infinity))
  const nearby = active.filter((route) => (routeDistances.get(route.id) || Infinity) <= NEARBY_ROUTE_FEET)
  const farther = active.filter((route) => !nearby.includes(route))
  const inactive = routes.filter((route) => !liveRouteIds.has(route.id))
  return <div className="panel-scroll routes-panel"><div className="panel-title-row"><div><p className="eyebrow">ROUTES</p><h1>Live bus lines</h1></div></div><RouteGroup title="Nearby routes" routes={nearby} selectedRoute={selectedRoute} distances={routeDistances} onRoute={onRoute} /><RouteGroup title={nearby.length ? 'All routes' : 'Live routes'} routes={farther.length || !nearby.length ? (farther.length ? farther : active) : []} selectedRoute={selectedRoute} distances={routeDistances} onRoute={onRoute} /><RouteGroup title="Not running now" routes={inactive} selectedRoute={selectedRoute} distances={routeDistances} onRoute={onRoute} inactive /></div>
}

function RouteDetailPanel({ route, detail, selectedVehicle, location, onStop, onBack }: { route?: Route; detail?: RouteDetail; selectedVehicle?: Vehicle; location: LocationState; onStop: (stop: Stop) => void; onBack: () => void }) {
  if (!route || !detail) return <div className="panel-scroll routes-panel"><p className="eyebrow">ROUTE</p><h1>Loading route…</h1></div>
  const nearby = location.coordinates ? detail.stops.map((stop) => ({ stop, feet: distanceFeet(location.coordinates!, stop) })).sort((a, b) => a.feet - b.feet).slice(0, 3) : []
  const nextStop = selectedVehicle?.next_stop_id ? detail.stops.find((stop) => stop.id === selectedVehicle.next_stop_id)?.name : undefined
  return <div className="panel-scroll route-detail-panel"><div className="panel-title-row"><div><p className="eyebrow">ROUTE</p><h1>{route.name}</h1></div><button className="close-panel" onClick={onBack} aria-label="Back to routes">×</button></div>{selectedVehicle && <p className="vehicle-note">Bus {selectedVehicle.vehicle_id || selectedVehicle.id}{nextStop ? ` · Next: ${nextStop}` : ''}</p>}{location.coordinates ? <section className="route-nearby"><h2 className="section-heading">Nearby stops</h2>{nearby.map(({ stop, feet }) => <button className="stop-pick" key={stop.id} onClick={() => onStop({ ...stop, routes: [route.id] })}><span className="stop-pick-dot" /><span><strong>{stop.name}</strong><small>{route.short_name || route.name}</small></span><b>{formatFeet(feet)}</b></button>)}</section> : <div className="location-card"><div><strong>Nearby stops on this route</strong><p>{location.error || 'Use your location to see the closest stops.'}</p></div><button onClick={location.request}>Use location</button></div>}<RouteTimeline route={route} stops={detail.stops} vehicles={detail.vehicles} arrivals={detail.arrivals || []} location={location.coordinates} /></div>
}

function RouteGroup({ title, routes, selectedRoute, distances, onRoute, inactive = false }: { title: string; routes: Route[]; selectedRoute?: string; distances: Map<string, number>; onRoute: (routeId: string) => void; inactive?: boolean }) {
  if (!routes.length) return null
  return <section className={`route-group ${inactive ? 'is-inactive' : ''}`}><h2 className="section-heading">{title}</h2>{routes.map((route) => <button key={route.id} className={selectedRoute === route.id ? 'route-row is-selected' : 'route-row'} disabled={inactive} style={{ '--route-color': route.color } as CSSProperties} onClick={() => onRoute(route.id)}><i /><span><strong>{route.short_name || route.name}</strong><small>{route.long_name || route.name}</small></span>{!inactive && Number.isFinite(distances.get(route.id)) && <b>{formatFeet(distances.get(route.id)!)}</b>}{inactive && <b>Inactive</b>}</button>)}</section>
}

function useSheetDrag(expanded: boolean, setExpanded: (expanded: boolean) => void) {
  const [offset, setOffset] = useState(0)
  const [isDragging, setIsDragging] = useState(false)
  const start = useRef<{ y: number; lastY: number; lastTime: number; velocity: number } | undefined>(undefined)
  const suppressClick = useRef(false)
  const travel = () => Math.max(258, Math.min(450, window.innerHeight * .5)) - 28
  const onPointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    start.current = { y: event.clientY, lastY: event.clientY, lastTime: event.timeStamp, velocity: 0 }
    setIsDragging(true)
  }
  const onPointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const gesture = start.current
    if (!gesture) return
    const raw = event.clientY - gesture.y
    const min = expanded ? 0 : -travel()
    const max = expanded ? travel() : 0
    const resisted = raw < min ? min + (raw - min) * .18 : raw > max ? max + (raw - max) * .18 : raw
    const elapsed = Math.max(1, event.timeStamp - gesture.lastTime)
    gesture.velocity = (event.clientY - gesture.lastY) / elapsed
    gesture.lastY = event.clientY
    gesture.lastTime = event.timeStamp
    if (Math.abs(raw) > 3) suppressClick.current = true
    setOffset(resisted)
  }
  const finish = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const gesture = start.current
    if (!gesture) return
    const raw = event.clientY - gesture.y
    const velocity = gesture.velocity
    const threshold = travel() * .42
    const next = Math.abs(velocity) > .3 ? velocity < 0 : expanded ? raw < threshold : raw < -threshold
    start.current = undefined
    setOffset(0)
    setIsDragging(false)
    setExpanded(next)
    if (suppressClick.current) requestAnimationFrame(() => { suppressClick.current = false })
  }
  return {
    isDragging,
    style: { '--sheet-drag': `${offset}px` } as CSSProperties,
    onPointerDown,
    onPointerMove,
    onPointerUp: finish,
    onPointerCancel: finish,
    onClick: () => {
      if (suppressClick.current) return
      setExpanded(!expanded)
    },
  }
}

function useUserLocation(): LocationState {
  const [coordinates, setCoordinates] = useState<Coordinates>()
  const [error, setError] = useState<string>()
  const request = useCallback(() => {
    if (!navigator.geolocation) { setError('This browser does not provide location access.'); return }
    navigator.geolocation.getCurrentPosition((position) => { setCoordinates({ latitude: position.coords.latitude, longitude: position.coords.longitude }); setError(undefined) }, () => setError('Location was not available. You can still browse every stop.'), { enableHighAccuracy: true, maximumAge: 60_000, timeout: 10_000 })
  }, [])
  return { coordinates, error, request }
}

function distanceByRoute(routes: Route[], stops: Stop[], from?: Coordinates) {
  const distances = new Map<string, number>()
  if (!from) return distances
  for (const route of routes) {
    const routeStops = stops.filter((stop) => stop.routes.includes(route.id))
    distances.set(route.id, Math.min(...routeStops.map((stop) => distanceFeet(from, stop))))
  }
  return distances
}

function distanceFeet(from: Coordinates, to: Coordinates) {
  const earthRadiusFeet = 20_925_524.9
  const radians = Math.PI / 180
  const dLat = (to.latitude - from.latitude) * radians
  const dLng = (to.longitude - from.longitude) * radians
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(from.latitude * radians) * Math.cos(to.latitude * radians) * Math.sin(dLng / 2) ** 2
  return earthRadiusFeet * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

function nearestBusFeet(vehicles: Vehicle[], routeId: string, stop: Stop) {
  const values = vehicles.filter((vehicle) => vehicle.route_id === routeId && Number.isFinite(vehicle.latitude) && Number.isFinite(vehicle.longitude)).map((vehicle) => distanceFeet(vehicle, stop))
  return values.length ? Math.min(...values) : Infinity
}

function stopIndexForVehicle(vehicle: Vehicle, stops: Stop[]) {
  if (vehicle.next_stop_id) {
    const nextIndex = stops.findIndex((stop) => stop.id === vehicle.next_stop_id)
    if (nextIndex >= 0) return nextIndex
  }
  const distances = stops.map((stop) => distanceFeet(vehicle, stop))
  return distances.length ? distances.indexOf(Math.min(...distances)) : -1
}

function timelinePosition(index: number, length: number) { return length > 1 ? 4 + (index / (length - 1)) * 92 : 50 }
function formatFeet(feet: number) { return `${Math.max(0, Math.round(feet / 10) * 10).toLocaleString()} ft` }
function firstArrivalLabel(arrivals: Arrival[]) { return arrivalLabelsForRoute(arrivals)[0] }
function arrivalLabelsForRoute(arrivals: Arrival[]) { return arrivals.map((arrival) => minutesUntil(arrival.our_eta || arrival.passio_eta)).filter((label) => label !== 'No estimate') }
function routeText(route: Route) { return `${route.name} ${route.short_name || ''} ${route.long_name || ''}`.toLowerCase() }
function readRecentStops() { try { const value: unknown = JSON.parse(localStorage.getItem(RECENT_STOPS_KEY) || '[]'); return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string').slice(0, 6) : [] } catch { return [] } }

function useSseRefresh() {
  const [lastEvent, setLastEvent] = useState(0)
  useEffect(() => { const stream = new EventSource('/api/v1/stream'); stream.addEventListener('vehicles', () => setLastEvent(Date.now())); return () => stream.close() }, [])
  return lastEvent
}

function Diagnostics() {
  const query = useQuery({ queryKey: ['diagnostics'], queryFn: api.diagnostics, refetchInterval: 10_000 })
  return <main className="diagnostics"><a href="/">← Rider map</a><p className="eyebrow">PIPELINE DIAGNOSTICS</p><h1>UGA Bus collector</h1>{query.isLoading && <p>Loading diagnostics…</p>}{query.isError && <p>This diagnostics endpoint is protected or unavailable.</p>}{query.data && <pre>{JSON.stringify(query.data, null, 2)}</pre>}</main>
}
