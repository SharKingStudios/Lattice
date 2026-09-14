import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl, { Map as MapLibreMap, Marker } from 'maplibre-gl'
import type { FeatureCollection, LineString, Point } from 'geojson'
import type { Route, Stop, Vehicle } from './api'

type Shape = { routeId: string; color: string; coordinates: [number, number][]; shapeId: string }

type Props = { routes: Route[]; shapes: Shape[]; stops: Stop[]; vehicles: Vehicle[]; selectedRoute?: string; selectedStop?: string; onStop: (stop: Stop) => void; onVehicle: (vehicle: Vehicle) => void }
const initialCenter: [number, number] = [-83.378, 33.951]

export function TransitMap({ routes, shapes, stops, vehicles, selectedRoute, selectedStop, onStop, onVehicle }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const markers = useRef(new globalThis.Map<string, Marker>())
  const stopsById = useMemo(() => new globalThis.Map(stops.map((stop) => [stop.id, stop])), [stops])
  const stopsByIdRef = useRef(stopsById)
  stopsByIdRef.current = stopsById
  const stopHandler = useRef(onStop)
  const vehicleHandler = useRef(onVehicle)
  const [loaded, setLoaded] = useState(false)
  stopHandler.current = onStop
  vehicleHandler.current = onVehicle

  useEffect(() => {
    if (!host.current || map.current) return
    const instance = new maplibregl.Map({ container: host.current, style: import.meta.env.VITE_MAP_STYLE || 'https://tiles.openfreemap.org/styles/liberty', center: initialCenter, zoom: 14.25, attributionControl: { compact: true }, maxPitch: 55 })
    map.current = instance
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    instance.on('load', () => {
      instance.addSource('route-shapes', { type: 'geojson', data: emptyLines() })
      instance.addLayer({ id: 'route-underlay', type: 'line', source: 'route-shapes', paint: { 'line-color': '#fffaf1', 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 3, 16, 7], 'line-opacity': .78 } })
      instance.addLayer({ id: 'route-lines', type: 'line', source: 'route-shapes', paint: { 'line-color': ['get', 'color'], 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1.5, 16, 4.5], 'line-opacity': ['case', ['get', 'selected'], 1, .38] } })
      instance.addSource('stops', { type: 'geojson', data: emptyPoints() })
      instance.addLayer({ id: 'stop-halo', type: 'circle', source: 'stops', paint: { 'circle-radius': ['case', ['get', 'selected'], 9, 6], 'circle-color': '#fffaf1', 'circle-opacity': .96 } })
      instance.addLayer({ id: 'stop-dots', type: 'circle', source: 'stops', paint: { 'circle-radius': ['case', ['get', 'selected'], 5.2, 3.3], 'circle-color': ['case', ['get', 'selected'], '#111111', '#f5c242'], 'circle-stroke-width': 1, 'circle-stroke-color': '#161415' } })
      instance.on('mouseenter', 'stops', () => { instance.getCanvas().style.cursor = 'pointer' })
      instance.on('mouseleave', 'stops', () => { instance.getCanvas().style.cursor = '' })
      instance.on('click', 'stops', (event) => {
        const id = event.features?.[0]?.properties?.id as string | undefined
        const stop = id ? stopsByIdRef.current.get(id) : undefined
        if (stop) stopHandler.current(stop)
      })
      setLoaded(true)
    })
    return () => { markers.current.forEach((marker) => marker.remove()); markers.current.clear(); instance.remove(); map.current = null; setLoaded(false) }
  }, [])

  useEffect(() => {
    const instance = map.current
    if (!loaded || !instance?.isStyleLoaded()) return
    const source = instance.getSource('route-shapes') as maplibregl.GeoJSONSource | undefined
    source?.setData({ type: 'FeatureCollection', features: shapes.filter((shape) => shape.coordinates.length > 1).map((shape) => ({ type: 'Feature', properties: { routeId: shape.routeId, color: shape.color, selected: selectedRoute === shape.routeId }, geometry: { type: 'LineString', coordinates: shape.coordinates } })) })
  }, [shapes, selectedRoute, loaded])

  useEffect(() => {
    const instance = map.current
    if (!loaded || !instance?.isStyleLoaded()) return
    const source = instance.getSource('stops') as maplibregl.GeoJSONSource | undefined
    source?.setData({ type: 'FeatureCollection', features: stops.filter((stop) => Number.isFinite(stop.latitude) && Number.isFinite(stop.longitude)).map((stop) => ({ type: 'Feature', properties: { id: stop.id, selected: selectedStop === stop.id }, geometry: { type: 'Point', coordinates: [stop.longitude, stop.latitude] } })) })
  }, [stops, selectedStop, loaded])

  useEffect(() => {
    const instance = map.current
    if (!loaded || !instance) return
    const present = new Set<string>()
    for (const vehicle of vehicles) {
      if (!Number.isFinite(vehicle.latitude) || !Number.isFinite(vehicle.longitude)) continue
      present.add(vehicle.id)
      let marker = markers.current.get(vehicle.id)
      if (!marker) {
        const element = document.createElement('button')
        element.className = 'bus-marker'
        element.ariaLabel = `Bus ${vehicle.vehicle_id || vehicle.id}`
        element.innerHTML = '<span>●</span>'
        element.addEventListener('click', () => vehicleHandler.current(vehicle))
        marker = new maplibregl.Marker({ element, rotationAlignment: 'map' }).setLngLat([vehicle.longitude, vehicle.latitude]).addTo(instance)
        markers.current.set(vehicle.id, marker)
      } else {
        marker.setLngLat([vehicle.longitude, vehicle.latitude])
      }
      marker.getElement().style.setProperty('--bus-color', routes.find((route) => route.id === vehicle.route_id)?.color || '#ba0c2f')
      marker.setRotation(vehicle.bearing || 0)
    }
    markers.current.forEach((marker, id) => { if (!present.has(id)) { marker.remove(); markers.current.delete(id) } })
  }, [vehicles, routes, loaded])

  return <div ref={host} className="transit-map" role="application" aria-label="UGA Campus Transit map" />
}

function emptyLines(): FeatureCollection<LineString> { return { type: 'FeatureCollection', features: [] } }
function emptyPoints(): FeatureCollection<Point> { return { type: 'FeatureCollection', features: [] } }
