# UGA Bus

UGA Bus is a fast, map-first view of University of Georgia Campus Transit. It collects the official Passio GTFS and GTFS-Realtime feeds once on the server, keeps the latest state in memory, saves normalized history in SQLite, and serves unlimited riders from that local cache.

It is deliberately a transit-data project first: live Passio predictions are retained instead of overwritten, vehicle observations are map-matched against the exact GTFS shapes, conservative stop events are inferred, and the system can evaluate future local ETA estimates against both ground truth and Passio.

## Status

This repository contains the complete first-pass service, UI, test suite, real-feed smoke tool, and reproducible Nest deployment configuration. It does **not** contain live history or Nest/DNS credentials. The app must run continuously before its own ETA model can be trusted; until then it transparently falls back to Passio predictions when available.

## Architecture

```text
 official Passio static GTFS / GTFS-RT
                 │  (one responsible server-side poller per feed)
                 ▼
  Collector → validation/versioned GTFS + SQLite history → in-memory live cache
                 │                         │                    │
                 │                         └── matching, stop events, ETA evaluation
                 ▼                                              ▼
         /api/v1 REST + SSE  ─────────────────────────────→ browser map clients
```

The browser never contacts `passio3.com`. Vehicle positions default to an 8-second server poll, trip updates to 10 seconds, alerts to 45 seconds, and static GTFS to one hour. Each failed upstream request backs off exponentially (up to five minutes), while the last known data remains visible with a stale warning.

## Sources

- Static: `https://passio3.com/uga/passioTransit/gtfs/google_transit.zip`
- Vehicle positions: `https://passio3.com/uga/passioTransit/gtfs/realtime/vehiclePositions`
- Trip updates: `https://passio3.com/uga/passioTransit/gtfs/realtime/tripUpdates`
- Service alerts: `https://passio3.com/uga/passioTransit/gtfs/realtime/serviceAlerts`

Transitland catalogues this as UGA feed `f-uga~ga~us`; the smoke test checks these upstream URLs directly rather than a proxy.

## Local setup

Python 3.12+ and Node 20+ are required.

```bash
npm install
npm run web:build
python3 -m venv .venv
. .venv/bin/activate
pip install -r apps/api/requirements-dev.txt
export PYTHONPATH="$PWD/apps/api/src"
python -m uga_bus.cli migrate
RUN_REAL_FEED=1 pytest tests/api
uvicorn uga_bus.app:app --reload
```

The Vite frontend proxies `/api` to port 8000 during development:

```bash
npm run web:dev
```

Default local data is `./data/uga_bus.sqlite3`; SQLite runs in WAL mode to keep reader requests cheap while collectors write. Do not commit this directory.

## API

All endpoints are same-origin below `/api/v1`:

- `health`, `meta`
- `routes`, `routes/{route_id}`
- `stops`, `stops/{stop_id}`, `arrivals?stop_id=…`
- `vehicles?route_id=…`, `alerts`, `stream` (SSE)
- `diagnostics` (set `UGA_BUS_DIAGNOSTICS_TOKEN` to require `Authorization: Bearer …`)

Responses include short cache directives and ETags where safe. Live data comes only from the cache. `/health` distinguishes process, database/static-feed availability, individual upstream ages, and degradation.

## Data operations

The data tools are all explicit and inspect the persistent database rather than live-browser state:

```bash
python -m uga_bus.cli fetch-static
python -m uga_bus.cli inspect-gtfs
python -m uga_bus.cli routes
python -m uga_bus.cli decode-realtime vehicles
python -m uga_bus.cli vehicles --vehicle-id 1234
python -m uga_bus.cli trip TRIP_ID
python -m uga_bus.cli predictions STOP_ID
python -m uga_bus.cli rematch
python -m uga_bus.cli rebuild-events
python -m uga_bus.cli recompute-segments
python -m uga_bus.cli evaluate
python -m uga_bus.cli db-stats
python -m uga_bus.cli prune
```

Detailed observations default to 180 days; compressed raw snapshot payloads are intended to be retained for seven days. Retention is configured via `.env`. The backup timer creates a consistent SQLite backup, verifies integrity and gzip output, and rotates archives older than 14 days.

## Tests and smoke checks

```bash
npm run web:test
npm run web:build
npm run feed:smoke
RUN_REAL_FEED=1 pytest tests/api
npm run web:e2e
```

`feed:smoke` downloads the current real UGA static feed, enumerates/decompresses its GTFS tables, checks required tables, and checks all three RT payloads look like protobuf feed messages. The Python opt-in integration test decodes the official protobufs using the same bindings used in production. E2E mocks the public API only so it can cover UI error/stale paths deterministically.

For a lightweight cached-endpoint load check after starting the API:

```bash
API_BASE=http://127.0.0.1:8000 npm run load
```

## Production operation

The checked-in `infra/` files are the production record: systemd runs the collector as the unprivileged `uga-bus` user, Caddy serves the built map and API from one origin, and a systemd timer creates verified, compressed SQLite backups.

Initial server install (the procedure used for this deployment):

```bash
git clone REPOSITORY_URL /opt/uga-bus
cd /opt/uga-bus
bash infra/install-nest.sh
sudoedit /etc/uga-bus/uga-bus.env
```

For an update, build the client locally, copy the checkout without `data/` or `.env`, then run the idempotent migration and `systemctl restart uga-bus`. The helper at `infra/deploy-nest.sh` performs this on Unix-like workstations; on Windows use `infra/deploy-nest.ps1 -NestHost HOST`. Check data and collector health with:

```bash
systemctl status uga-bus
journalctl -u uga-bus -f
sudo -u uga-bus PYTHONPATH=/opt/uga-bus/apps/api/src /opt/uga-bus/.venv/bin/python -m uga_bus.cli db-stats
```

`bus.loganpeterson.org` must resolve to the Nest server’s public IPv4 before Caddy can obtain HTTPS. The service itself can collect data before DNS/TLS is enabled.

## ETA methodology and known limits

The current ETA chooser has an explicit hierarchy: high-quality local segment history → conservative geometric/current-speed estimate → GTFS schedule → current Passio trip update → unknown. The shipped endpoint currently uses the Passio fallback while local segment-stat lookup is still being populated; it does not claim to be better yet.

Read [the ETA design](docs/eta.md), [data model](docs/data-model.md), and [data-quality notes](docs/data-quality.md) before interpreting metrics.
