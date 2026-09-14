from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Any

import httpx
from google.transit import gtfs_realtime_pb2
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, settings
from .db import (
    EtaPrediction,
    FeedVersion,
    RealtimeSnapshot,
    SegmentStatistic,
    ServiceAlert,
    ShapePoint,
    Stop,
    StopEvent,
    StopTime,
    Trip,
    UpstreamPrediction,
    VehicleObservation,
    as_utc,
    json_dumps,
    session_scope,
    utcnow,
)
from .eta import choose_eta
from .gtfs import ingest_gtfs
from .matching import match_confidence, project_on_polyline

logger = logging.getLogger(__name__)


def proto_timestamp(value: int) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


def enum_value(message: Any, field: str) -> int | None:
    return getattr(message, field) if message.HasField(field) else None


def translation_text(message: Any) -> str | None:
    if not message.translation:
        return None
    return next((item.text for item in message.translation if item.text), None)


@dataclass
class FeedHealth:
    name: str
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    last_error: str | None = None
    last_entities: int = 0
    identical_fetches: int = 0
    consecutive_failures: int = 0
    next_allowed_at: datetime | None = None

    def age_seconds(self) -> float | None:
        return (utcnow() - self.last_success).total_seconds() if self.last_success else None


@dataclass
class LiveCache:
    vehicles: dict[str, dict[str, Any]] = field(default_factory=dict)
    trip_updates: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    health: dict[str, FeedHealth] = field(
        default_factory=lambda: {
            name: FeedHealth(name) for name in ("static", "vehicles", "trip_updates", "alerts")
        }
    )
    version: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def publish(self) -> None:
        self.version += 1
        self.changed.set()

    async def wait_for_change(self, previous_version: int, timeout: float = 25.0) -> int:
        if self.version != previous_version:
            return self.version
        self.changed.clear()
        try:
            await asyncio.wait_for(self.changed.wait(), timeout=timeout)
        except TimeoutError:
            pass
        return self.version


class Collector:
    def __init__(self, configuration: Settings = settings) -> None:
        self.settings = configuration
        self.cache = LiveCache()
        self._client = httpx.AsyncClient(
            timeout=configuration.request_timeout_seconds,
            headers={"User-Agent": configuration.user_agent, "Accept-Encoding": "gzip, deflate"},
            follow_redirects=True,
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()
        self._last_checksums: dict[str, str] = {}

    async def start(self) -> None:
        await self.refresh_static()
        await asyncio.gather(self.refresh_vehicles(), self.refresh_trip_updates(), self.refresh_alerts())
        self._tasks = [
            asyncio.create_task(self._poll("static", self.settings.static_poll_seconds, self.refresh_static)),
            asyncio.create_task(
                self._poll("vehicles", self.settings.vehicle_poll_seconds, self.refresh_vehicles)
            ),
            asyncio.create_task(
                self._poll("trip_updates", self.settings.trip_update_poll_seconds, self.refresh_trip_updates)
            ),
            asyncio.create_task(self._poll("alerts", self.settings.alert_poll_seconds, self.refresh_alerts)),
        ]

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._client.aclose()

    async def _poll(self, name: str, interval: int, operation: Callable[[], Awaitable[None]]) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(interval)
            try:
                await operation()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("collector loop failed", extra={"feed": name})

    async def _download(self, name: str, url: str) -> tuple[bytes, dict[str, str], datetime, datetime] | None:
        health = self.cache.health[name]
        now = utcnow()
        if health.next_allowed_at and now < health.next_allowed_at:
            return None
        health.last_attempt = now
        started = utcnow()
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            ended = utcnow()
            health.last_success, health.last_error, health.consecutive_failures = ended, None, 0
            return response.content, dict(response.headers), started, ended
        except (httpx.HTTPError, OSError) as exc:
            health.consecutive_failures += 1
            health.last_error = f"{type(exc).__name__}: {exc}"[:500]
            # A capped exponential delay avoids retry storms while preserving last-known cache.
            health.next_allowed_at = utcnow() + timedelta(
                seconds=min(300, 2 ** min(health.consecutive_failures, 8))
            )
            logger.warning(
                "upstream fetch failed",
                extra={"feed": name, "failures": health.consecutive_failures, "error": health.last_error},
            )
            return None

    async def refresh_static(self) -> None:
        result = await self._download("static", self.settings.static_gtfs_url)
        if not result:
            return
        payload, headers, _, _ = result
        version, changed = await asyncio.to_thread(
            lambda: _sync_ingest(payload, self.settings.static_gtfs_url, headers)
        )
        self.cache.health["static"].last_entities = _row_count(version.row_counts_json)
        self.cache.health["static"].identical_fetches += int(not changed)
        if changed:
            self.cache.publish()
            logger.info(
                "static GTFS version activated",
                extra={"checksum": version.checksum, "feed_version": version.feed_version},
            )

    async def refresh_vehicles(self) -> None:
        result = await self._download("vehicles", self.settings.vehicle_positions_url)
        if not result:
            return
        payload, _, started, ended = result
        parsed = await asyncio.to_thread(self._store_vehicle_payload, payload, started, ended)
        if parsed is not None:
            self.cache.vehicles = {item["id"]: item for item in parsed}
            self.cache.health["vehicles"].last_entities = len(parsed)
            self.cache.publish()

    async def refresh_trip_updates(self) -> None:
        result = await self._download("trip_updates", self.settings.trip_updates_url)
        if not result:
            return
        payload, _, started, ended = result
        parsed = await asyncio.to_thread(self._store_trip_updates, payload, started, ended)
        if parsed is not None:
            self.cache.trip_updates = parsed
            self.cache.health["trip_updates"].last_entities = len(parsed)
            self.cache.publish()

    async def refresh_alerts(self) -> None:
        result = await self._download("alerts", self.settings.service_alerts_url)
        if not result:
            return
        payload, _, started, ended = result
        parsed = await asyncio.to_thread(self._store_alerts, payload, started, ended)
        if parsed is not None:
            self.cache.alerts = parsed
            self.cache.health["alerts"].last_entities = len(parsed)
            self.cache.publish()

    def _snapshot(
        self, session: Session, feed_type: str, payload: bytes, started: datetime, ended: datetime
    ) -> tuple[RealtimeSnapshot, gtfs_realtime_pb2.FeedMessage | None]:
        digest = hashlib.sha256(payload).hexdigest()
        feed = gtfs_realtime_pb2.FeedMessage()
        parse_error = None
        try:
            feed.ParseFromString(payload)
        except Exception as exc:
            parse_error = f"{type(exc).__name__}: {exc}"[:1000]
        previous = self._last_checksums.get(feed_type)
        snapshot = RealtimeSnapshot(
            feed_type=feed_type,
            download_started_at=started,
            download_ended_at=ended,
            http_status=200,
            payload_size=len(payload),
            checksum=digest,
            feed_timestamp=proto_timestamp(feed.header.timestamp) if not parse_error else None,
            entity_count=len(feed.entity) if not parse_error else 0,
            parsed_entities=0,
            parse_error=parse_error,
            latency_ms=(ended - started).total_seconds() * 1000,
            identical_to_previous=digest == previous,
            raw_payload=payload,
        )
        session.add(snapshot)
        session.flush()
        self._last_checksums[feed_type] = digest
        if digest == previous:
            self.cache.health[feed_type].identical_fetches += 1
        return snapshot, None if parse_error else feed

    def _store_vehicle_payload(
        self, payload: bytes, started: datetime, ended: datetime
    ) -> list[dict[str, Any]] | None:
        with session_scope() as session:
            snapshot, feed = self._snapshot(session, "vehicles", payload, started, ended)
            if not feed:
                return None
            active = session.scalar(select(FeedVersion).where(FeedVersion.is_active.is_(True)))
            output: list[dict[str, Any]] = []
            parsed = 0
            for entity in feed.entity:
                if not entity.HasField("vehicle"):
                    continue
                try:
                    vehicle = entity.vehicle
                    position = vehicle.position if vehicle.HasField("position") else None
                    trip_descriptor = vehicle.trip if vehicle.HasField("trip") else None
                    trip_id = trip_descriptor.trip_id or None if trip_descriptor else None
                    route_id = trip_descriptor.route_id or None if trip_descriptor else None
                    record = {
                        "id": vehicle.vehicle.id or entity.id,
                        "entity_id": entity.id or None,
                        "vehicle_id": vehicle.vehicle.id or None,
                        "trip_id": trip_id,
                        "route_id": route_id,
                        "direction_id": enum_value(trip_descriptor, "direction_id")
                        if trip_descriptor
                        else None,
                        "latitude": position.latitude if position else None,
                        "longitude": position.longitude if position else None,
                        "bearing": position.bearing if position and position.HasField("bearing") else None,
                        "speed": position.speed if position and position.HasField("speed") else None,
                        "current_stop_sequence": vehicle.current_stop_sequence
                        if vehicle.HasField("current_stop_sequence")
                        else None,
                        "stop_id": vehicle.stop_id or None,
                        "current_status": enum_value(vehicle, "current_status"),
                        "congestion_level": enum_value(vehicle, "congestion_level"),
                        "occupancy_status": enum_value(vehicle, "occupancy_status"),
                        "timestamp": proto_timestamp(vehicle.timestamp) or utcnow(),
                    }
                    match = self._match_vehicle(session, active, record)
                    observation = VehicleObservation(
                        snapshot_id=snapshot.id,
                        feed_version_id=active.id if active else None,
                        observed_at=utcnow(),
                        feed_timestamp=proto_timestamp(feed.header.timestamp),
                        entity_id=record["entity_id"],
                        vehicle_id=record["vehicle_id"],
                        trip_id=trip_id,
                        route_id=route_id,
                        direction_id=record["direction_id"],
                        latitude=record["latitude"],
                        longitude=record["longitude"],
                        bearing=record["bearing"],
                        speed=record["speed"],
                        current_stop_sequence=record["current_stop_sequence"],
                        stop_id=record["stop_id"],
                        current_status=record["current_status"],
                        congestion_level=record["congestion_level"],
                        occupancy_status=record["occupancy_status"],
                        raw_json=json_dumps(record),
                        **match,
                    )
                    session.add(observation)
                    self._infer_stop_event(session, active, observation)
                    output.append({**record, "timestamp": record["timestamp"].isoformat(), **match})
                    parsed += 1
                except Exception as exc:
                    logger.warning(
                        "malformed vehicle entity skipped: %s",
                        exc,
                        extra={"entity": entity.id, "error": str(exc)[:300]},
                    )
            snapshot.parsed_entities = parsed
            return output

    def _match_vehicle(
        self, session: Session, active: FeedVersion | None, record: dict[str, Any]
    ) -> dict[str, Any]:
        empty = {
            "matched_trip_id": None,
            "matched_shape_id": None,
            "progress_meters": None,
            "cross_track_meters": None,
            "previous_stop_id": None,
            "next_stop_id": None,
            "match_confidence": 0.0,
            "match_method": "unmatched",
        }
        if not active or record["latitude"] is None or record["longitude"] is None:
            return empty
        trip = None
        method = "trip_id"
        if record["trip_id"]:
            trip = session.scalar(
                select(Trip).where(Trip.feed_version_id == active.id, Trip.trip_id == record["trip_id"])
            )
        if not trip and record["route_id"]:
            # Conservative fallback: only choose a route trip with an actual shape; confidence stays lower.
            trip = session.scalars(
                select(Trip)
                .where(
                    Trip.feed_version_id == active.id,
                    Trip.route_id == record["route_id"],
                    Trip.shape_id.is_not(None),
                )
                .limit(1)
            ).first()
            method = "route_shape_candidate"
        if not trip or not trip.shape_id:
            return empty
        points = session.scalars(
            select(ShapePoint)
            .where(ShapePoint.feed_version_id == active.id, ShapePoint.shape_id == trip.shape_id)
            .order_by(ShapePoint.sequence)
        ).all()
        projection = project_on_polyline(
            record["latitude"], record["longitude"], ((p.latitude, p.longitude) for p in points)
        )
        if not projection:
            return empty
        stop_times = session.scalars(
            select(StopTime)
            .where(StopTime.feed_version_id == active.id, StopTime.trip_id == trip.trip_id)
            .order_by(StopTime.stop_sequence)
        ).all()
        before = next(
            (
                st
                for st in reversed(stop_times)
                if record["current_stop_sequence"] and st.stop_sequence < record["current_stop_sequence"]
            ),
            None,
        )
        after = next(
            (
                st
                for st in stop_times
                if not record["current_stop_sequence"] or st.stop_sequence >= record["current_stop_sequence"]
            ),
            None,
        )
        previous = session.scalar(
            select(VehicleObservation)
            .where(
                VehicleObservation.vehicle_id == record["vehicle_id"],
                VehicleObservation.matched_shape_id == trip.shape_id,
            )
            .order_by(VehicleObservation.observed_at.desc())
        )
        penalty = (
            0.35
            if previous
            and previous.progress_meters
            and projection.progress_meters + 250 < previous.progress_meters
            else 0.0
        )
        confidence = match_confidence(
            projection.cross_track_meters, bool(record["trip_id"] == trip.trip_id), penalty
        )
        return {
            "matched_trip_id": trip.trip_id,
            "matched_shape_id": trip.shape_id,
            "progress_meters": projection.progress_meters,
            "cross_track_meters": projection.cross_track_meters,
            "previous_stop_id": before.stop_id if before else None,
            "next_stop_id": after.stop_id if after else None,
            "match_confidence": confidence,
            "match_method": method if confidence >= 0.2 else "low_confidence",
        }

    def _infer_stop_event(
        self, session: Session, active: FeedVersion | None, observation: VehicleObservation
    ) -> None:
        if (
            not active
            or not observation.vehicle_id
            or not observation.next_stop_id
            or observation.cross_track_meters is None
        ):
            return
        last_observation = session.scalar(
            select(VehicleObservation)
            .where(
                VehicleObservation.vehicle_id == observation.vehicle_id,
                VehicleObservation.id != observation.id,
            )
            .order_by(VehicleObservation.observed_at.desc())
        )
        if (
            last_observation
            and last_observation.next_stop_id
            and last_observation.next_stop_id != observation.next_stop_id
        ):
            prior_event = session.scalars(
                select(StopEvent)
                .where(
                    StopEvent.vehicle_id == observation.vehicle_id,
                    StopEvent.stop_id == last_observation.next_stop_id,
                    StopEvent.departure_at.is_(None),
                )
                .order_by(StopEvent.arrival_at.desc())
                .limit(1)
            ).first()
            if prior_event and as_utc(prior_event.arrival_at) <= observation.observed_at:
                prior_event.departure_at = observation.observed_at
                prior_event.dwell_seconds = (
                    observation.observed_at - as_utc(prior_event.arrival_at)
                ).total_seconds()
        previous = session.scalar(
            select(VehicleObservation)
            .where(
                VehicleObservation.vehicle_id == observation.vehicle_id,
                VehicleObservation.next_stop_id == observation.next_stop_id,
                VehicleObservation.id != observation.id,
            )
            .order_by(VehicleObservation.observed_at.desc())
        )
        if not previous or as_utc(previous.observed_at) < observation.observed_at - timedelta(seconds=90):
            return
        stopped = observation.speed is None or observation.speed < 2
        near = (
            observation.cross_track_meters < 55
            and previous.cross_track_meters is not None
            and previous.cross_track_meters < 55
        )
        existing = session.scalar(
            select(StopEvent).where(
                StopEvent.vehicle_id == observation.vehicle_id,
                StopEvent.stop_id == observation.next_stop_id,
                StopEvent.arrival_at > observation.observed_at - timedelta(minutes=5),
            )
        )
        if near and stopped and not existing:
            session.add(
                StopEvent(
                    feed_version_id=active.id,
                    vehicle_id=observation.vehicle_id,
                    trip_id=observation.matched_trip_id or observation.trip_id,
                    route_id=observation.route_id,
                    stop_id=observation.next_stop_id,
                    stop_sequence=observation.current_stop_sequence,
                    arrival_at=as_utc(previous.observed_at),
                    confidence=min(0.9, (observation.match_confidence or 0) * 0.8),
                    inference_method="two_nearby_low_speed_observations",
                )
            )

    def _store_trip_updates(
        self, payload: bytes, started: datetime, ended: datetime
    ) -> list[dict[str, Any]] | None:
        with session_scope() as session:
            snapshot, feed = self._snapshot(session, "trip_updates", payload, started, ended)
            if not feed:
                return None
            output: list[dict[str, Any]] = []
            parsed = 0
            active = session.scalar(select(FeedVersion).where(FeedVersion.is_active.is_(True)))
            for entity in feed.entity:
                if not entity.HasField("trip_update"):
                    continue
                try:
                    update = entity.trip_update
                    trip_id = update.trip.trip_id or None
                    route_id = update.trip.route_id or None
                    if not route_id and active and trip_id:
                        trip = session.scalar(
                            select(Trip).where(Trip.feed_version_id == active.id, Trip.trip_id == trip_id)
                        )
                        route_id = trip.route_id if trip else None
                    one = {
                        "entity_id": entity.id,
                        "trip_id": trip_id,
                        "route_id": route_id,
                        "vehicle_id": update.vehicle.id or None,
                        "updates": [],
                    }
                    for stop_update in update.stop_time_update:
                        arrival = (
                            proto_timestamp(stop_update.arrival.time)
                            if stop_update.HasField("arrival")
                            else None
                        )
                        departure = (
                            proto_timestamp(stop_update.departure.time)
                            if stop_update.HasField("departure")
                            else None
                        )
                        delay = (
                            stop_update.arrival.delay
                            if stop_update.HasField("arrival") and stop_update.arrival.HasField("delay")
                            else (
                                stop_update.departure.delay
                                if stop_update.HasField("departure")
                                and stop_update.departure.HasField("delay")
                                else None
                            )
                        )
                        if not stop_update.stop_id:
                            continue
                        prediction = UpstreamPrediction(
                            snapshot_id=snapshot.id,
                            observed_at=utcnow(),
                            feed_timestamp=proto_timestamp(feed.header.timestamp),
                            entity_id=entity.id or None,
                            vehicle_id=update.vehicle.id or None,
                            trip_id=trip_id,
                            route_id=route_id,
                            stop_id=stop_update.stop_id,
                            stop_sequence=stop_update.stop_sequence
                            if stop_update.HasField("stop_sequence")
                            else None,
                            predicted_arrival=arrival,
                            predicted_departure=departure,
                            delay_seconds=delay,
                            raw_json=json_dumps({"schedule_relationship": stop_update.schedule_relationship}),
                        )
                        session.add(prediction)
                        # Capture our contemporaneous baseline. When every remaining segment
                        # has local history it wins; otherwise the source records the fallback.
                        estimate = self._baseline_estimate(
                            session,
                            active,
                            trip_id,
                            route_id,
                            prediction.vehicle_id,
                            prediction.stop_id,
                            prediction.stop_sequence,
                            prediction.observed_at,
                            arrival,
                        )
                        session.add(
                            EtaPrediction(
                                observed_at=prediction.observed_at,
                                vehicle_id=prediction.vehicle_id,
                                trip_id=trip_id,
                                route_id=route_id,
                                stop_id=stop_update.stop_id,
                                estimated_arrival=estimate.arrival,
                                lower_arrival=estimate.lower,
                                upper_arrival=estimate.upper,
                                confidence=estimate.confidence,
                                source=estimate.source,
                                details_json=json_dumps({"detail": estimate.detail}),
                            )
                        )
                        one["updates"].append(
                            {
                                "stop_id": stop_update.stop_id,
                                "stop_sequence": prediction.stop_sequence,
                                "arrival": arrival.isoformat() if arrival else None,
                                "departure": departure.isoformat() if departure else None,
                                "delay_seconds": delay,
                            }
                        )
                    output.append(one)
                    parsed += 1
                except Exception as exc:
                    logger.warning(
                        "malformed trip update skipped", extra={"entity": entity.id, "error": str(exc)[:300]}
                    )
            snapshot.parsed_entities = parsed
            return output

    def _baseline_estimate(
        self,
        session: Session,
        active: FeedVersion | None,
        trip_id: str | None,
        route_id: str | None,
        vehicle_id: str | None,
        target_stop_id: str,
        target_sequence: int | None,
        now: datetime,
        passio_arrival: datetime | None,
    ):
        """Use local segment history only when every remaining segment is well supported."""
        if not active or not trip_id or not route_id:
            return choose_eta(now=now, passio_arrival=passio_arrival)
        vehicle = (
            session.scalars(
                select(VehicleObservation)
                .where(VehicleObservation.vehicle_id == vehicle_id)
                .order_by(VehicleObservation.observed_at.desc())
                .limit(1)
            ).first()
            if vehicle_id
            else None
        )
        segments: list[float] = []
        current_sequence = vehicle.current_stop_sequence if vehicle else None
        if (
            current_sequence is not None
            and target_sequence is not None
            and target_sequence > current_sequence
        ):
            stop_times = session.scalars(
                select(StopTime)
                .where(
                    StopTime.feed_version_id == active.id,
                    StopTime.trip_id == trip_id,
                    StopTime.stop_sequence >= current_sequence,
                    StopTime.stop_sequence <= target_sequence,
                )
                .order_by(StopTime.stop_sequence)
            ).all()
            bucket = f"weekday-{now.hour // 3 * 3:02d}"
            for origin, destination in zip(stop_times, stop_times[1:]):
                statistic = session.scalar(
                    select(SegmentStatistic).where(
                        SegmentStatistic.feed_version_id == active.id,
                        SegmentStatistic.route_id == route_id,
                        SegmentStatistic.from_stop_id == origin.stop_id,
                        SegmentStatistic.to_stop_id == destination.stop_id,
                        SegmentStatistic.service_bucket == bucket,
                        SegmentStatistic.sample_count >= 3,
                    )
                )
                if not statistic:
                    segments = []
                    break
                segments.append(statistic.mean_seconds)
        target = session.scalar(
            select(Stop).where(Stop.feed_version_id == active.id, Stop.stop_id == target_stop_id)
        )
        geometric_seconds = None
        if (
            vehicle
            and vehicle.latitude is not None
            and vehicle.longitude is not None
            and vehicle.speed
            and vehicle.speed > 1
            and target
            and target.latitude is not None
            and target.longitude is not None
        ):
            geometric_seconds = (
                _haversine_meters(vehicle.latitude, vehicle.longitude, target.latitude, target.longitude)
                / vehicle.speed
            )
        return choose_eta(
            now=now,
            segment_seconds=segments,
            geometric_seconds=geometric_seconds,
            passio_arrival=passio_arrival,
        )

    def _store_alerts(
        self, payload: bytes, started: datetime, ended: datetime
    ) -> list[dict[str, Any]] | None:
        with session_scope() as session:
            snapshot, feed = self._snapshot(session, "alerts", payload, started, ended)
            if not feed:
                return None
            output: list[dict[str, Any]] = []
            parsed = 0
            for entity in feed.entity:
                if not entity.HasField("alert"):
                    continue
                try:
                    alert = entity.alert
                    periods = [
                        {
                            "start": proto_timestamp(p.start).isoformat() if p.start else None,
                            "end": proto_timestamp(p.end).isoformat() if p.end else None,
                        }
                        for p in alert.active_period
                    ]
                    informed = [
                        {
                            "route_id": item.route_id or None,
                            "stop_id": item.stop_id or None,
                            "trip_id": item.trip.trip_id if item.HasField("trip") else None,
                        }
                        for item in alert.informed_entity
                    ]
                    header, description = (
                        translation_text(alert.header_text),
                        translation_text(alert.description_text),
                    )
                    session.add(
                        ServiceAlert(
                            snapshot_id=snapshot.id,
                            observed_at=utcnow(),
                            entity_id=entity.id or None,
                            cause=enum_value(alert, "cause"),
                            effect=enum_value(alert, "effect"),
                            header=header,
                            description=description,
                            active_periods_json=json_dumps(periods),
                            informed_entities_json=json_dumps(informed),
                        )
                    )
                    output.append(
                        {
                            "id": entity.id,
                            "header": header,
                            "description": description,
                            "periods": periods,
                            "informed": informed,
                        }
                    )
                    parsed += 1
                except Exception as exc:
                    logger.warning(
                        "malformed service alert skipped",
                        extra={"entity": entity.id, "error": str(exc)[:300]},
                    )
            snapshot.parsed_entities = parsed
            return output


def _sync_ingest(payload: bytes, source_url: str, headers: dict[str, str]) -> tuple[FeedVersion, bool]:
    with session_scope() as session:
        version, changed = ingest_gtfs(session, payload, source_url, headers)
        session.flush()
        # Detach scalar fields before the session is closed.
        session.expunge(version)
        return version, changed


def _row_count(row_counts_json: str) -> int:
    try:
        import json

        return sum(json.loads(row_counts_json).values())
    except Exception:
        return 0


def _haversine_meters(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    d_lat, d_lon = radians(latitude_b - latitude_a), radians(longitude_b - longitude_a)
    value = sin(d_lat / 2) ** 2 + cos(radians(latitude_a)) * cos(radians(latitude_b)) * sin(d_lon / 2) ** 2
    return 6_371_000 * 2 * asin(sqrt(value))
