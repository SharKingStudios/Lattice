import { expect, test } from '@playwright/test'

const routes = { routes: [{ id: 'Orbit', short_name: 'Orbit', long_name: 'Orbit', name: 'Orbit', color: '#ba0c2f', text_color: '#fff', active: true }] }
const stops = { stops: [{ id: 'tate', name: 'Tate Center', latitude: 33.957, longitude: -83.377, routes: ['Orbit'] }] }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/routes', (route) => route.fulfill({ json: routes }))
  await page.route('**/api/v1/stops', (route) => route.fulfill({ json: stops }))
  await page.route('**/api/v1/vehicles', (route) => route.fulfill({ json: { vehicles: [], stale: true } }))
  await page.route('**/api/v1/health', (route) => route.fulfill({ json: { status: 'degraded', upstream: {} } }))
  await page.route('**/api/v1/routes/Orbit', (route) => route.fulfill({ json: { route: routes.routes[0], shapes: [{ shape_id: 'orbit', direction_ids: [0], coordinates: [[-83.38, 33.95], [-83.377, 33.957]] }], stops: stops.stops, vehicles: [] } }))
  await page.route('**/api/v1/stops/tate', (route) => route.fulfill({ json: { stop: stops.stops[0], arrivals: [], data_fresh: false } }))
})

test('map rider flow has route, stop and stale-data affordances', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByLabel('UGA Campus Transit map')).toBeVisible()
  await page.getByRole('button', { name: 'Orbit' }).click()
  await page.getByPlaceholder('Find a route or stop').fill('Tate')
  await page.getByRole('button', { name: 'Tate Center' }).click()
  await expect(page.getByRole('heading', { name: 'Tate Center' })).toBeVisible()
  await expect(page.getByText('Live information is delayed')).toBeVisible()
})
