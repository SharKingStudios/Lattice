from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from . import __version__
from .collector import Collector
from .config import settings
from .db import (
    EtaPrediction,
    FeedVersion,
    Route,
    SegmentStatistic,
    ShapePoint,
    Stop,
    StopEvent,
    StopTime,
    Trip,
    UpstreamPrediction,
    VehicleObservation,
    as_utc,
    create_schema,
    session_scope,
    utcnow,
)
from .eta import choose_eta, prediction_metrics
from .gtfs import is_service_active

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
EASTERN = ZoneInfo("America/New_York")


@asynccontextmanager
async def lifespan(application: FastAPI):
    create_schema()
    collector = Collector()
    application.state.collector = collector
    await collector.start()
    try:
        yield
    finally:
        await collector.stop()


app = FastAPI(title="UGA Bus API", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=800)
origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"], max_age=3600
    )


def get_session():
    with session_scope() as session:
        yield session


def active_feed(session: Session) -> FeedVersion:
    feed = session.scalar(select(FeedVersion).where(FeedVersion.is_active.is_(True)))
    if not feed:
        raise HTTPException(503, "Static GTFS has not loaded yet")
    return feed


def fresh(health_name: str) -> bool:
    age = app.state.collector.cache.health[health_name].age_seconds()
    return age is not None and age <= settings.stale_after_seconds


def cached_json(request: Request, value: object, seconds: int = 10) -> JSONResponse:
    encoded = jsonable_encoder(value)
    body = json.dumps(encoded, separators=(",", ":")).encode()
    etag = f'"{hashlib.sha256(body).hexdigest()[:24]}"'
    headers = {
        "Cache-Control": f"public, max-age={seconds}, stale-while-revalidate={seconds * 3}",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return JSONResponse(status_code=304, content=None, headers=headers)
    return JSONResponse(content=encoded, headers=headers)


def route_wire(route: Route, active: bool | None = None) -> dict[str, object]:
    color = (route.color or "").strip("#")
    if len(color) not in (3, 6) or not all(c in "0123456789abcdefABCDEF" for c in color):
        color = "BA0C2F"  # restrained UGA red fallback; static feed color wins when valid.
    return {
        "id": route.route_id,
        "short_name": route.short_name,
        "long_name": route.long_name,
        "name": route.long_name or route.short_name or route.route_id,
        "color": f"#{color}",
        "text_color": f"#{(route.text_color or 'FFFFFF').strip('#')}",
        "sort_order": route.sort_order,
        "active": active,
    }


def current_service_ids(session: Session, feed: FeedVersion) -> set[str]:
    today = datetime.now(EASTERN).date()
    service_ids = session.scalars(
        select(distinct(Trip.service_id)).where(Trip.feed_version_id == feed.id)
    ).all()
    return {
        service_id for service_id in service_ids if is_service_active(session, feed.id, service_id, today)
    }


@app.get("/api/v1/health")
def health(request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    try:
        feed = session.scalar(select(FeedVersion).where(FeedVersion.is_active.is_(True)))
        db_ok = True
    except Exception:
        feed, db_ok = None, False
    collector = app.state.collector
    feeds = {
        name: {
            "last_success": state.last_success,
            "age_seconds": state.age_seconds(),
            "fresh": fresh(name),
            "consecutive_failures": state.consecutive_failures,
            "last_error": state.last_error,
        }
        for name, state in collector.cache.health.items()
    }
    healthy = db_ok and bool(feed) and fresh("vehicles")
    return cached_json(
        request,
        {
            "status": "ok" if healthy else "degraded",
            "process": "alive",
            "database": "ok" if db_ok else "unavailable",
            "static_loaded": bool(feed),
            "active_gtfs_checksum": feed.checksum if feed else None,
            "upstream": feeds,
            "cache_version": collector.cache.version,
        },
        3,
    )


@app.get("/api/v1/meta")
def meta(request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    feed = active_feed(session)
    return cached_json(
        request,
        {
            "application_version": __version__,
            "active_gtfs": {
                "checksum": feed.checksum,
                "feed_version": feed.feed_version,
                "fetched_at": feed.fetched_at,
                "service_date_range": [feed.start_date, feed.end_date],
                "validation": json.loads(feed.validation_json),
            },
            "freshness": {
                name: state.age_seconds() for name, state in app.state.collector.cache.health.items()
            },
            "timezone": "America/New_York",
            "stream": "/api/v1/stream",
        },
        30,
    )


@app.get("/api/v1/routes")
def routes(request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    feed = active_feed(session)
    active_services = current_service_ids(session, feed)
    active_route_ids = (
        set(
            session.scalars(
                select(distinct(Trip.route_id)).where(
                    Trip.feed_version_id == feed.id, Trip.service_id.in_(active_services)
                )
            ).all()
        )
        if active_services
        else set()
    )
    data = [
        route_wire(route, route.route_id in active_route_ids)
        for route in session.scalars(
            select(Route)
            .where(Route.feed_version_id == feed.id)
            .order_by(Route.sort_order, Route.short_name, Route.long_name)
        ).all()
    ]
    return cached_json(
        request, {"routes": data, "generated_at": utcnow(), "data_fresh": fresh("vehicles")}, 30
    )


@app.get("/api/v1/routes/{route_id}")
def route_detail(route_id: str, request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    feed = active_feed(session)
    route = session.scalar(select(Route).where(Route.feed_version_id == feed.id, Route.route_id == route_id))
    if not route:
        raise HTTPException(404, "Route not found")
    trips = session.scalars(
        select(Trip).where(Trip.feed_version_id == feed.id, Trip.route_id == route_id)
    ).all()
    shapes: list[dict[str, object]] = []
    for shape_id in sorted({trip.shape_id for trip in trips if trip.shape_id}):
        points = session.scalars(
            select(ShapePoint)
            .where(ShapePoint.feed_version_id == feed.id, ShapePoint.shape_id == shape_id)
            .order_by(ShapePoint.sequence)
        ).all()
        directions = sorted(
            {
                trip.direction_id
                for trip in trips
                if trip.shape_id == shape_id and trip.direction_id is not None
            }
        )
        shapes.append(
            {
                "shape_id": shape_id,
                "direction_ids": directions,
                "coordinates": [[point.longitude, point.latitude] for point in points],
            }
        )
    representative = trips[0] if trips else None
    stop_list = []
    if representative:
        stop_times = session.scalars(
            select(StopTime)
            .where(StopTime.feed_version_id == feed.id, StopTime.trip_id == representative.trip_id)
            .order_by(StopTime.stop_sequence)
        ).all()
        stops = {
            stop.stop_id: stop
            for stop in session.scalars(
                select(Stop).where(
                    Stop.feed_version_id == feed.id, Stop.stop_id.in_([st.stop_id for st in stop_times])
                )
            ).all()
        }
        stop_list = [
            {
                "id": st.stop_id,
                "name": stops[st.stop_id].name if st.stop_id in stops else st.stop_id,
                "sequence": st.stop_sequence,
                "latitude": stops[st.stop_id].latitude if st.stop_id in stops else None,
                "longitude": stops[st.stop_id].longitude if st.stop_id in stops else None,
            }
            for st in stop_times
        ]
    vehicles = [
        vehicle
        for vehicle in app.state.collector.cache.vehicles.values()
        if vehicle.get("route_id") == route_id
    ]
    return cached_json(
        request,
        {
            "route": route_wire(route),
            "shapes": shapes,
            "stops": stop_list,
            "vehicles": vehicles,
            "arrivals": build_route_arrivals(session, feed, route_id),
            "data_fresh": fresh("vehicles"),
        },
        15,
    )


@app.get("/api/v1/stops")
def stops(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=500, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> JSONResponse:
    feed = active_feed(session)
    stmt = select(Stop).where(Stop.feed_version_id == feed.id)
    if q:
        normalized = " ".join(q.split())
        stmt = stmt.where(func.lower(Stop.name).contains(normalized.lower()))
    rows = session.scalars(stmt.order_by(Stop.name).limit(limit)).all()
    stop_ids = [row.stop_id for row in rows]
    by_stop: dict[str, set[str]] = defaultdict(set)
    if stop_ids:
        for trip_id, stop_id in session.execute(
            select(StopTime.trip_id, StopTime.stop_id).where(
                StopTime.feed_version_id == feed.id, StopTime.stop_id.in_(stop_ids)
            )
        ).all():
            route_id = session.scalar(
                select(Trip.route_id).where(Trip.feed_version_id == feed.id, Trip.trip_id == trip_id)
            )
            if route_id:
                by_stop[stop_id].add(route_id)
    data = [
        {
            "id": stop.stop_id,
            "code": stop.code,
            "name": stop.name,
            "latitude": stop.latitude,
            "longitude": stop.longitude,
            "parent_station": stop.parent_station,
            "routes": sorted(by_stop[stop.stop_id]),
        }
        for stop in rows
    ]
    return cached_json(request, {"stops": data}, 120)


@app.get("/api/v1/stops/{stop_id}")
def stop_detail(stop_id: str, request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    feed = active_feed(session)
    stop = session.scalar(select(Stop).where(Stop.feed_version_id == feed.id, Stop.stop_id == stop_id))
    if not stop:
        raise HTTPException(404, "Stop not found")
    arrivals_data = build_arrivals(session, feed, stop_id)
    return cached_json(
        request,
        {
            "stop": {
                "id": stop.stop_id,
                "name": stop.name,
                "code": stop.code,
                "latitude": stop.latitude,
                "longitude": stop.longitude,
            },
            "arrivals": arrivals_data,
            "data_fresh": fresh("trip_updates"),
        },
        5,
    )


@app.get("/api/v1/vehicles")
def vehicles(request: Request, route_id: str | None = Query(default=None, max_length=255)) -> JSONResponse:
    values = list(app.state.collector.cache.vehicles.values())
    if route_id:
        values = [item for item in values if item.get("route_id") == route_id]
    return cached_json(
        request,
        {
            "vehicles": values,
            "generated_at": utcnow(),
            "data_age_seconds": app.state.collector.cache.health["vehicles"].age_seconds(),
            "stale": not fresh("vehicles"),
        },
        2,
    )


@app.get("/api/v1/arrivals")
def arrivals(
    request: Request,
    stop_id: str = Query(min_length=1, max_length=255),
    session: Session = Depends(get_session),
) -> JSONResponse:
    feed = active_feed(session)
    return cached_json(
        request,
        {
            "stop_id": stop_id,
            "arrivals": build_arrivals(session, feed, stop_id),
            "data_age_seconds": app.state.collector.cache.health["trip_updates"].age_seconds(),
            "stale": not fresh("trip_updates"),
        },
        4,
    )


def build_arrivals(session: Session, feed: FeedVersion, stop_id: str) -> list[dict[str, object]]:
    return build_predictions(session, feed, stop_id=stop_id)


def build_route_arrivals(session: Session, feed: FeedVersion, route_id: str) -> list[dict[str, object]]:
    return build_predictions(session, feed, route_id=route_id)


def build_predictions(
    session: Session,
    feed: FeedVersion,
    *,
    stop_id: str | None = None,
    route_id: str | None = None,
) -> list[dict[str, object]]:
    now = utcnow()
    candidates: list[dict[str, object]] = []
    route_cache: dict[str, Route | None] = {}
    for update in app.state.collector.cache.trip_updates:
        update_route_id = update.get("route_id")
        if route_id and update_route_id != route_id:
            continue
        for prediction in update["updates"]:
            if stop_id and prediction["stop_id"] != stop_id:
                continue
            passio = datetime.fromisoformat(prediction["arrival"]) if prediction["arrival"] else None
            if passio and passio <= now:
                continue
            if update_route_id not in route_cache:
                route_cache[update_route_id] = (
                    session.scalar(
                        select(Route).where(
                            Route.feed_version_id == feed.id, Route.route_id == update_route_id
                        )
                    )
                    if update_route_id
                    else None
                )
            route = route_cache[update_route_id]
            recorded_query = (
                select(EtaPrediction)
                .where(
                    EtaPrediction.trip_id == update.get("trip_id"),
                    EtaPrediction.stop_id == prediction["stop_id"],
                )
                .order_by(EtaPrediction.observed_at.desc())
                .limit(1)
            )
            if update.get("vehicle_id"):
                recorded_query = recorded_query.where(EtaPrediction.vehicle_id == update["vehicle_id"])
            recorded = session.scalars(recorded_query).first()
            estimate = choose_eta(now=now, passio_arrival=passio)
            reliable_local = bool(
                recorded
                and recorded.source == "uga_estimation"
                and recorded.estimated_arrival
                and as_utc(recorded.estimated_arrival) > now
            )
            candidates.append(
                {
                    "route_id": update_route_id,
                    "route": route_wire(route) if route else None,
                    "vehicle_id": update.get("vehicle_id"),
                    "trip_id": update.get("trip_id"),
                    "stop_id": prediction["stop_id"],
                    "stop_sequence": prediction.get("stop_sequence"),
                    "our_eta": as_utc(recorded.estimated_arrival) if reliable_local else estimate.arrival,
                    "eta_range": [as_utc(recorded.lower_arrival), as_utc(recorded.upper_arrival)]
                    if reliable_local and recorded and recorded.lower_arrival
                    else ([estimate.lower, estimate.upper] if estimate.lower else None),
                    "confidence": recorded.confidence if reliable_local and recorded else estimate.confidence,
                    "prediction_source": recorded.source if reliable_local and recorded else estimate.source,
                    "passio_eta": passio,
                    "scheduled_eta": None,
                    "data_age_seconds": app.state.collector.cache.health["trip_updates"].age_seconds(),
                }
            )
    ordered = sorted(
        candidates, key=lambda item: item["our_eta"] or item["passio_eta"] or datetime.max.replace(tzinfo=UTC)
    )
    return ordered[: settings.max_arrivals_per_stop] if stop_id else ordered


@app.get("/api/v1/alerts")
def alerts(request: Request) -> JSONResponse:
    return cached_json(
        request,
        {
            "alerts": app.state.collector.cache.alerts,
            "data_age_seconds": app.state.collector.cache.health["alerts"].age_seconds(),
            "stale": not fresh("alerts"),
        },
        20,
    )


@app.get("/api/v1/stream")
async def stream(request: Request) -> StreamingResponse:
    async def events():
        version = -1
        while not await request.is_disconnected():
            version = await app.state.collector.cache.wait_for_change(version)
            data = json.dumps(
                {
                    "version": version,
                    "vehicles": list(app.state.collector.cache.vehicles.values()),
                    "vehicle_age_seconds": app.state.collector.cache.health["vehicles"].age_seconds(),
                    "stale": not fresh("vehicles"),
                },
                default=str,
                separators=(",", ":"),
            )
            yield f"event: vehicles\ndata: {data}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/diagnostics")
def diagnostics(request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    if (
        settings.diagnostics_token
        and request.headers.get("Authorization") != f"Bearer {settings.diagnostics_token}"
    ):
        raise HTTPException(404, "Not found")
    feed = active_feed(session)
    segment_statistics = session.scalars(select(SegmentStatistic)).all()
    diagnostics = {
        "feed": {
            "checksum": feed.checksum,
            "version": feed.feed_version,
            "validation": json.loads(feed.validation_json),
            "row_counts": json.loads(feed.row_counts_json),
        },
        "upstream": {
            name: {
                "age_seconds": status.age_seconds(),
                "last_error": status.last_error,
                "entity_count": status.last_entities,
                "identical_fetches": status.identical_fetches,
                "consecutive_failures": status.consecutive_failures,
            }
            for name, status in app.state.collector.cache.health.items()
        },
        "latest": {
            "active_buses": len(app.state.collector.cache.vehicles),
            "vehicle_observations": session.scalar(select(func.count(VehicleObservation.id))) or 0,
            "trip_predictions": session.scalar(select(func.count(UpstreamPrediction.id))) or 0,
            "stop_events": session.scalar(select(func.count(StopEvent.id))) or 0,
            "low_confidence_matches": session.scalar(
                select(func.count(VehicleObservation.id)).where(VehicleObservation.match_confidence < 0.4)
            )
            or 0,
            "unmatched_vehicles": sum(
                1 for vehicle in app.state.collector.cache.vehicles.values() if not vehicle.get("trip_id")
            ),
            "database_bytes": settings.sqlite_path.stat().st_size
            if settings.sqlite_path and settings.sqlite_path.exists()
            else None,
        },
        "learning": {
            "timezone": settings.learning_timezone,
            "refresh_seconds": settings.learning_refresh_seconds,
            "segment_statistics": len(segment_statistics),
            "usable_segments": sum(
                statistic.sample_count >= settings.learning_min_segment_samples
                for statistic in segment_statistics
            ),
            "segment_samples": sum(statistic.sample_count for statistic in segment_statistics),
            "last_refresh": app.state.collector.cache.health["learning"].last_success,
            "last_error": app.state.collector.cache.health["learning"].last_error,
        },
        "evaluation": evaluate_predictions(session),
    }
    return cached_json(request, diagnostics, 10)


def evaluate_predictions(session: Session) -> dict[str, object]:
    # Measure forecasts at the same lead time. Comparing the final update immediately
    # before a bus reaches a stop would make every system look artificially accurate.
    horizons = (2, 5, 10, 20)
    by_horizon: dict[str, dict[str, list[tuple[datetime, datetime]]]] = {
        str(minutes): {"passio": [], "uga_estimation": []} for minutes in horizons
    }
    events = session.scalars(
        select(StopEvent)
        .where(StopEvent.confidence >= 0.5, StopEvent.trip_id.is_not(None))
        .order_by(StopEvent.arrival_at.desc())
        .limit(500)
    ).all()
    for event in events:
        for minutes in horizons:
            cutoff = as_utc(event.arrival_at) - timedelta(minutes=minutes)
            prediction = session.scalars(
                select(UpstreamPrediction)
                .where(
                    UpstreamPrediction.trip_id == event.trip_id,
                    UpstreamPrediction.stop_id == event.stop_id,
                    UpstreamPrediction.predicted_arrival.is_not(None),
                    UpstreamPrediction.observed_at <= cutoff,
                )
                .order_by(UpstreamPrediction.observed_at.desc())
                .limit(1)
            ).first()
            uga = session.scalars(
                select(EtaPrediction)
                .where(
                    EtaPrediction.trip_id == event.trip_id,
                    EtaPrediction.stop_id == event.stop_id,
                    EtaPrediction.source == "uga_estimation",
                    EtaPrediction.estimated_arrival.is_not(None),
                    EtaPrediction.observed_at <= cutoff,
                )
                .order_by(EtaPrediction.observed_at.desc())
                .limit(1)
            ).first()
            if prediction and prediction.predicted_arrival:
                by_horizon[str(minutes)]["passio"].append(
                    (prediction.predicted_arrival, event.arrival_at)
                )
            if uga and uga.estimated_arrival:
                by_horizon[str(minutes)]["uga_estimation"].append(
                    (uga.estimated_arrival, event.arrival_at)
                )
    return {
        "by_horizon_minutes": {
            horizon: {name: prediction_metrics(pairs) for name, pairs in sources.items()}
            for horizon, sources in by_horizon.items()
        },
        "event_sample": len(events),
        "note": "Each forecast is compared with an inferred arrival at the same lead time; low-confidence arrivals are excluded.",
    }


web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
if web_dist.exists():
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
