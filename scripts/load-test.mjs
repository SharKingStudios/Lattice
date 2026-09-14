const base = process.env.API_BASE || 'http://127.0.0.1:8000'
const requests = Number(process.env.REQUESTS || 200)
const concurrency = Number(process.env.CONCURRENCY || 25)
let cursor = 0
const latencies = []
let failures = 0

async function worker() {
  while (cursor < requests) {
    cursor++
    const started = performance.now()
    try {
      const response = await fetch(`${base}/api/v1/vehicles`)
      if (!response.ok) failures++
      await response.arrayBuffer()
      latencies.push(performance.now() - started)
    } catch { failures++ }
  }
}

await Promise.all(Array.from({ length: concurrency }, worker))
latencies.sort((a, b) => a - b)
const percentile = (p) => latencies[Math.min(latencies.length - 1, Math.floor((latencies.length - 1) * p))] || null
const result = { endpoint: `${base}/api/v1/vehicles`, requests, concurrency, successful: latencies.length - failures, failures, p50_ms: percentile(.5), p95_ms: percentile(.95), max_ms: percentile(1) }
console.log(JSON.stringify(result, null, 2))
if (failures) process.exitCode = 1
