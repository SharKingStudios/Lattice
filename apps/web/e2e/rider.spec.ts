import { expect, test } from '@playwright/test'

const routes = { routes: [{ id: 'Orbit', short_name: 'Orbit', long_name: 'Orbit', name: 'Orbit', color: '#ba0c2f', text_color: '#fff', active: true }] }
const stops = { stops: [{ id: 'tate', name: 'Tate Center', latitude: 33.957, longitude: -83.377, routes: ['Orbit'] }] }
const vehicles = { vehicles: [{ id: 'bus-1', vehicle_id: '1', route_id: 'Orbit', latitude: 33.956, longitude: -83.378, next_stop_id: 'tate' }], stale: true }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/routes', (route) => route.fulfill({ json: routes }))
  await page.route('**/api/v1/stops', (route) => route.fulfill({ json: stops }))
  await page.route('**/api/v1/vehicles', (route) => route.fulfill({ json: vehicles }))
  await page.route('**/api/v1/health', (route) => route.fulfill({ json: { status: 'degraded', upstream: {} } }))
  await page.route('**/api/v1/routes/Orbit', (route) => route.fulfill({ json: { route: routes.routes[0], shapes: [{ shape_id: 'orbit', direction_ids: [0], coordinates: [[-83.38, 33.95], [-83.377, 33.957]] }], stops: stops.stops, vehicles: [] } }))
  await page.route('**/api/v1/stops/tate', (route) => route.fulfill({ json: { stop: stops.stops[0], arrivals: [], data_fresh: false } }))
})

test('mobile rider flow has live routes, stops and stale-data affordances', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByLabel('UGA Campus Transit map')).toBeVisible()
  await page.getByRole('button', { name: 'Routes' }).click()
  await page.getByRole('button', { name: /^Orbit/ }).click()
  await page.getByPlaceholder('Find a route or stop').fill('Tate')
  await page.getByRole('button', { name: 'Tate Center' }).click()
  await expect(page.getByRole('heading', { name: 'Tate Center' })).toBeVisible()
  await expect(page.getByText('Route stops')).not.toBeVisible()
  await page.getByRole('button', { name: /^Orbit/ }).click()
  await expect(page.getByText('Route stops')).toBeVisible()
  await expect(page.getByText('Live information is delayed')).toBeVisible()
})

test('mobile sheet drags open while navigation remains available', async ({ page }) => {
  await page.goto('/')
  const grabber = page.getByRole('button', { name: 'Expand panel' })
  const box = await grabber.boundingBox()
  if (!box) throw new Error('Sheet grabber was not rendered')
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2, box.y - 180, { steps: 8 })
  await page.mouse.up()
  await expect(page.getByRole('heading', { name: '1 bus running' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Map' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stops' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Routes' })).toBeVisible()
})
