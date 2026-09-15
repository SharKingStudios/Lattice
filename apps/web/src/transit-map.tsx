import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl, { LngLatBounds, Map as MapLibreMap, Marker } from 'maplibre-gl'
import type { FeatureCollection, LineString, Point } from 'geojson'
import type { Route, Stop, Vehicle } from './api'

type Shape = { routeId: string; color: string; coordinates: [number, number][]; shapeId: string }
type Props = { routes: Route[]; shapes: Shape[]; stops: Stop[]; vehicles: Vehicle[]; activeRouteIds: Set<string>; selectedRoute?: string; selectedStop?: string; onStop: (stop: Stop) => void; onVehicle: (vehicle: Vehicle) => void }
const initialCenter: [number, number] = [-83.378, 33.951]

export function TransitMap({ routes, shapes, stops, vehicles, activeRouteIds, selectedRoute, selectedStop, onStop, onVehicle }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const markers = useRef(new globalThis.Map<string, Marker>())
  const stopsById = useMemo(() => new globalThis.Map(stops.map((stop) => [stop.id, stop])), [stops])
  const stopsByIdRef = useRef(stopsById)
  const stopHandler = useRef(onStop)
  const vehicleHandler = useRef(onVehicle)
  const vehiclesById = useRef(new globalThis.Map<string, Vehicle>())
  const initialFrameComplete = useRef(false)
  const framedRoute = useRef<string | undefined>(undefined)
  const [loaded, setLoaded] = useState(false)
  stopsByIdRef.current = stopsById
  stopHandler.current = onStop
  vehicleHandler.current = onVehicle

  useEffect(() => {
    if (!host.current || map.current) return
    const instance = new maplibregl.Map({
      container: host.current,
      style: import.meta.env.VITE_MAP_STYLE || 'https://tiles.openfreemap.org/styles/liberty',
      center: initialCenter,
      zoom: 14.25,
      maxZoom: 21,
      attributionControl: { compact: true },
      maxPitch: 55,
    })
    map.current = instance
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    instance.on('load', () => {
      // Show the named campus amenities and building footprints through a much wider useful zoom range.
      const campusDetailZooms: Record<string, number> = {
        building: 14,
        'building-3d': 15,
        poi_r1: 14,
        poi_r7: 15,
        poi_r20: 16,
      }
      for (const [layerId, minZoom] of Object.entries(campusDetailZooms)) {
        try { instance.setLayerZoomRange(layerId, minZoom, 24) } catch { /* A custom map style may omit this layer. */ }
      }
      instance.addSource('route-shapes', { type: 'geojson', data: emptyLines() })
      instance.addLayer({ id: 'route-underlay', type: 'line', source: 'route-shapes', paint: { 'line-color': '#fffaf1', 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 3, 16, 7], 'line-opacity': ['case', ['get', 'selected'], .9, ['get', 'active'], .5, .12] } })
      instance.addLayer({ id: 'route-lines', type: 'line', source: 'route-shapes', paint: { 'line-color': ['case', ['get', 'selected'], ['get', 'color'], ['get', 'active'], ['get', 'color'], '#7d807d'], 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1.5, 16, 4.5], 'line-opacity': ['case', ['get', 'selected'], 1, ['get', 'active'], .78, .16] } })
      instance.addSource('stops', { type: 'geojson', data: emptyPoints() })
      instance.addLayer({ id: 'stop-halo', type: 'circle', source: 'stops', paint: { 'circle-radius': ['case', ['get', 'selected'], 12, ['interpolate', ['linear'], ['zoom'], 12, 5.5, 16, 7]], 'circle-color': '#fffaf1', 'circle-stroke-width': 1, 'circle-stroke-color': '#4a4037', 'circle-opacity': .98 } })
      instance.addLayer({ id: 'stop-dots', type: 'circle', source: 'stops', paint: { 'circle-radius': ['case', ['get', 'selected'], 6.5, ['interpolate', ['linear'], ['zoom'], 12, 3.2, 16, 4.4]], 'circle-color': ['case', ['get', 'selected'], '#111111', '#f5c242'], 'circle-stroke-width': 1.4, 'circle-stroke-color': '#161415' } })
      instance.on('mouseenter', 'stops', () => { instance.getCanvas().style.cursor = 'pointer' })
      instance.on('mouseleave', 'stops', () => { instance.getCanvas().style.cursor = '' })
      instance.on('click', 'stops', (event) => {
        const id = event.features?.[0]?.properties?.id as string | undefined
        const stop = id ? stopsByIdRef.current.get(id) : undefined
        if (stop) stopHandler.current(stop)
      })
      setLoaded(true)
    })
    return () => {
      markers.current.forEach((marker) => marker.remove())
      markers.current.clear()
      instance.remove()
      map.current = null
      setLoaded(false)
    }
  }, [])

  useEffect(() => {
    const instance = map.current
    if (!loaded || !instance?.isStyleLoaded()) return
    const source = instance.getSource('route-shapes') as maplibregl.GeoJSONSource | undefined
    const visibleShapes = selectedRoute ? shapes.filter((shape) => shape.routeId === selectedRoute) : shapes
    source?.setData({
      type: 'FeatureCollection',
      features: visibleShapes.filter((shape) => shape.coordinates.length > 1).map((shape) => ({
        type: 'Feature',
        properties: { routeId: shape.routeId, color: shape.color, active: activeRouteIds.has(shape.routeId), selected: selectedRoute === shape.routeId },
        geometry: { type: 'LineString', coordinates: shape.coordinates },
      })),
    })
  }, [shapes, activeRouteIds, selectedRoute, loaded])

  useEffect(() => {
    const instance = map.current
    if (!loaded || !instance?.isStyleLoaded()) return
    const source = instance.getSource('stops') as maplibregl.GeoJSONSource | undefined
    source?.setData({
      type: 'FeatureCollection',
      features: stops.filter((stop) => Number.isFinite(stop.latitude) && Number.isFinite(stop.longitude)).map((stop) => ({ type: 'Feature', properties: { id: stop.id, selected: selectedStop === stop.id }, geometry: { type: 'Point', coordinates: [stop.longitude, stop.latitude] } })),
    })
  }, [stops, selectedStop, loaded])

  useEffect(() => {
    const instance = map.current
    if (!loaded || !instance) return
    vehiclesById.current = new globalThis.Map(vehicles.map((vehicle) => [vehicle.id, vehicle]))
    const present = new Set<string>()
    for (const vehicle of vehicles) {
      if (!Number.isFinite(vehicle.latitude) || !Number.isFinite(vehicle.longitude)) continue
      present.add(vehicle.id)
      let marker = markers.current.get(vehicle.id)
      if (!marker) {
        const element = document.createElement('button')
        element.className = 'bus-marker'
        element.ariaLabel = `Bus ${vehicle.vehicle_id || vehicle.id}`
        element.innerHTML = '<span>▸</span>'
        element.addEventListener('click', () => {
          const currentVehicle = vehiclesById.current.get(vehicle.id)
          if (currentVehicle) vehicleHandler.current(currentVehicle)
        })
        marker = new maplibregl.Marker({ element, rotationAlignment: 'map' }).setLngLat([vehicle.longitude, vehicle.latitude]).addTo(instance)
        markers.current.set(vehicle.id, marker)
      } else {
        marker.setLngLat([vehicle.longitude, vehicle.latitude])
      }
      const element = marker.getElement()
      element.style.setProperty('--bus-color', routes.find((route) => route.id === vehicle.route_id)?.color || '#ba0c2f')
      element.classList.toggle('is-dimmed', Boolean(selectedRoute && vehicle.route_id !== selectedRoute))
      element.classList.toggle('is-selected', vehicle.route_id === selectedRoute)
      marker.setRotation(vehicle.bearing || 0)
    }
    markers.current.forEach((marker, id) => { if (!present.has(id)) { marker.remove(); markers.current.delete(id) } })
  }, [vehicles, routes, selectedRoute, loaded])

  useEffect(() => {
    const instance = map.current
    if (!loaded || !instance || !shapes.length) return
    if (selectedRoute) {
      if (framedRoute.current === selectedRoute) return
      const routeShapes = shapes.filter((shape) => shape.routeId === selectedRoute)
      if (!routeShapes.length) return
      fitToShapes(instance, routeShapes)
      framedRoute.current = selectedRoute
      return
    }
    framedRoute.current = undefined
    if (initialFrameComplete.current || !activeRouteIds.size) return
    const everyLiveRouteHasShape = [...activeRouteIds].every((routeId) => shapes.some((shape) => shape.routeId === routeId))
    if (!everyLiveRouteHasShape) return
    fitToShapes(instance, shapes.filter((shape) => activeRouteIds.has(shape.routeId)))
    initialFrameComplete.current = true
  }, [activeRouteIds, loaded, selectedRoute, shapes])

  return <div ref={host} className="transit-map" role="application" aria-label="UGA Campus Transit map" />
}

function fitToShapes(map: MapLibreMap, shapes: Shape[]) {
  const bounds = new LngLatBounds()
  let count = 0
  for (const shape of shapes) for (const coordinate of shape.coordinates) { bounds.extend(coordinate); count += 1 }
  if (!count) return
  if (count === 1) map.easeTo({ center: bounds.getCenter(), zoom: 15, duration: 650 })
  else map.fitBounds(bounds, { padding: { top: 110, right: 38, bottom: 190, left: 38 }, maxZoom: 15.5, duration: 700 })
}

function emptyLines(): FeatureCollection<LineString> { return { type: 'FeatureCollection', features: [] } }
function emptyPoints(): FeatureCollection<Point> { return { type: 'FeatureCollection', features: [] } }
