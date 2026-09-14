import { inflateRawSync } from 'node:zlib'

const root = 'https://passio3.com/uga/passioTransit/gtfs'
const urls = {
  static: `${root}/google_transit.zip`,
  vehicles: `${root}/realtime/vehiclePositions`,
  tripUpdates: `${root}/realtime/tripUpdates`,
  alerts: `${root}/realtime/serviceAlerts`,
}

async function download(name, url) {
  const started = Date.now()
  const response = await fetch(url, { headers: { 'User-Agent': 'LatticeUGABus/0.1 (+https://bus.loganpeterson.org)' } })
  const bytes = new Uint8Array(await response.arrayBuffer())
  if (!response.ok || bytes.length === 0) throw new Error(`${name}: HTTP ${response.status}, ${bytes.length} bytes`)
  return { bytes, status: response.status, ms: Date.now() - started, type: response.headers.get('content-type'), etag: response.headers.get('etag') }
}

function gtfsFiles(buffer) {
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength)
  let eocd = -1
  for (let i = buffer.length - 22; i >= Math.max(0, buffer.length - 65_557); i--) if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break }
  if (eocd < 0) throw new Error('ZIP end-of-central-directory not found')
  const entries = view.getUint16(eocd + 10, true), centralOffset = view.getUint32(eocd + 16, true)
  const decoder = new TextDecoder()
  const output = new Map()
  let offset = centralOffset
  for (let count = 0; count < entries; count++) {
    if (view.getUint32(offset, true) !== 0x02014b50) throw new Error('Invalid ZIP central directory')
    const compression = view.getUint16(offset + 10, true), compressedSize = view.getUint32(offset + 20, true), nameLength = view.getUint16(offset + 28, true), extraLength = view.getUint16(offset + 30, true), commentLength = view.getUint16(offset + 32, true), localOffset = view.getUint32(offset + 42, true)
    const name = decoder.decode(buffer.subarray(offset + 46, offset + 46 + nameLength))
    const localNameLength = view.getUint16(localOffset + 26, true), localExtraLength = view.getUint16(localOffset + 28, true)
    const content = buffer.subarray(localOffset + 30 + localNameLength + localExtraLength, localOffset + 30 + localNameLength + localExtraLength + compressedSize)
    const plain = compression === 8 ? inflateRawSync(content) : content
    output.set(name, decoder.decode(plain))
    offset += 46 + nameLength + extraLength + commentLength
  }
  return output
}

function protobufLooksValid(bytes) {
  // GTFS-RT FeedMessage begins with field 1 (header). Alerts may validly contain no entities.
  return bytes.length > 2 && bytes[0] === 0x0a
}

const data = Object.fromEntries(await Promise.all(Object.entries(urls).map(async ([name, url]) => [name, await download(name, url)])))
const files = gtfsFiles(data.static.bytes)
const requiredTables = ['agency.txt', 'routes.txt', 'stops.txt', 'trips.txt', 'stop_times.txt']
const missing = requiredTables.filter((name) => !files.has(name))
if (missing.length) throw new Error(`Static GTFS missing required tables: ${missing.join(', ')}`)
for (const name of ['vehicles', 'tripUpdates', 'alerts']) if (!protobufLooksValid(data[name].bytes)) throw new Error(`${name} did not look like a GTFS-RT protobuf FeedMessage`)
const rowCounts = Object.fromEntries([...files].filter(([name]) => name.endsWith('.txt')).map(([name, text]) => [name, Math.max(0, text.trim().split(/\r?\n/).length - 1)]))
console.log(JSON.stringify({ checkedAt: new Date().toISOString(), static: { ...data.static, bytes: data.static.bytes.length, tables: [...files.keys()].sort(), rowCounts }, realtime: Object.fromEntries(['vehicles', 'tripUpdates', 'alerts'].map((name) => [name, { status: data[name].status, bytes: data[name].bytes.length, ms: data[name].ms, type: data[name].type, protobuf: true }])) }, null, 2))
