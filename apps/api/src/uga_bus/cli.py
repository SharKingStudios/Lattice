from __future__ import annotations

import argparse
import json
from datetime import timedelta
from urllib.request import Request, urlopen

from google.transit import gtfs_realtime_pb2
from sqlalchemy import delete, func, select, update

from .app import evaluate_predictions
from .collector import Collector
from .config import settings
from .db import (
    Base,
    FeedVersion,
    RealtimeSnapshot,
    StopEvent,
    Trip,
    UpstreamPrediction,
    VehicleObservation,
    create_schema,
    session_scope,
    utcnow,
)
from .gtfs import ingest_gtfs
from .learning import rebuild_segment_statistics


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


def fetch(url: str) -> tuple[bytes, dict[str, str]]:
    request = Request(url, headers={"User-Agent": settings.user_agent})
    with urlopen(request, timeout=settings.request_timeout_seconds) as response:
        return response.read(), dict(response.headers.items())


def active_feed(session):
    feed = session.scalar(select(FeedVersion).where(FeedVersion.is_active.is_(True)))
    if not feed:
        raise SystemExit("No GTFS feed loaded. Run fetch-static first.")
    return feed


def command_fetch_static(_: argparse.Namespace) -> None:
    payload, headers = fetch(settings.static_gtfs_url)
    with session_scope() as session:
        version, changed = ingest_gtfs(session, payload, settings.static_gtfs_url, headers)
        emit(
            {
                "changed": changed,
                "checksum": version.checksum,
                "feed_version": version.feed_version,
                "rows": json.loads(version.row_counts_json),
                "validation": json.loads(version.validation_json),
            }
        )


def command_inspect_gtfs(_: argparse.Namespace) -> None:
    with session_scope() as session:
        feed = active_feed(session)
        emit(
            {
                "checksum": feed.checksum,
                "feed_version": feed.feed_version,
                "row_counts": json.loads(feed.row_counts_json),
                "validation": json.loads(feed.validation_json),
                "service_date_range": [feed.start_date, feed.end_date],
            }
        )


def command_routes(_: argparse.Namespace) -> None:
    with session_scope() as session:
        feed = active_feed(session)
        from .db import Route

        emit(
            [
                {"id": r.route_id, "short_name": r.short_name, "long_name": r.long_name, "color": r.color}
                for r in session.scalars(
                    select(Route).where(Route.feed_version_id == feed.id).order_by(Route.short_name)
                ).all()
            ]
        )


def command_decode_realtime(args: argparse.Namespace) -> None:
    url = {
        "vehicles": settings.vehicle_positions_url,
        "trip-updates": settings.trip_updates_url,
        "alerts": settings.service_alerts_url,
    }[args.feed]
    payload, _ = fetch(url)
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    entities = []
    for entity in feed.entity:
        entities.append(
            json.loads(str(entity).replace("\n", " "))
            if False
            else {
                "id": entity.id,
                "has_vehicle": entity.HasField("vehicle"),
                "has_trip_update": entity.HasField("trip_update"),
                "has_alert": entity.HasField("alert"),
            }
        )
    emit(
        {
            "url": url,
            "bytes": len(payload),
            "header_timestamp": feed.header.timestamp,
            "entities": entities[: args.limit],
            "entity_count": len(feed.entity),
        }
    )


def command_vehicles(args: argparse.Namespace) -> None:
    with session_scope() as session:
        query = select(VehicleObservation).order_by(VehicleObservation.observed_at.desc()).limit(args.limit)
        if args.vehicle_id:
            query = query.where(VehicleObservation.vehicle_id == args.vehicle_id)
        rows = session.scalars(query).all()
        emit(
            [
                {
                    "observed_at": row.observed_at,
                    "vehicle_id": row.vehicle_id,
                    "trip_id": row.trip_id,
                    "route_id": row.route_id,
                    "coordinates": [row.longitude, row.latitude],
                    "match": {
                        "trip": row.matched_trip_id,
                        "shape": row.matched_shape_id,
                        "progress_meters": row.progress_meters,
                        "cross_track_meters": row.cross_track_meters,
                        "confidence": row.match_confidence,
                        "method": row.match_method,
                    },
                }
                for row in rows
            ]
        )


def command_trip(args: argparse.Namespace) -> None:
    from .db import StopTime

    with session_scope() as session:
        feed = active_feed(session)
        trip = session.scalar(
            select(Trip).where(Trip.feed_version_id == feed.id, Trip.trip_id == args.trip_id)
        )
        if not trip:
            raise SystemExit("Trip not found in active feed")
        rows = session.scalars(
            select(StopTime)
            .where(StopTime.feed_version_id == feed.id, StopTime.trip_id == args.trip_id)
            .order_by(StopTime.stop_sequence)
        ).all()
        emit(
            {
                "trip": {
                    "trip_id": trip.trip_id,
                    "route_id": trip.route_id,
                    "shape_id": trip.shape_id,
                    "headsign": trip.headsign,
                },
                "stops": [
                    {
                        "sequence": r.stop_sequence,
                        "stop_id": r.stop_id,
                        "arrival": r.arrival_time,
                        "departure": r.departure_time,
                    }
                    for r in rows
                ],
            }
        )


def command_predictions(args: argparse.Namespace) -> None:
    with session_scope() as session:
        rows = session.scalars(
            select(UpstreamPrediction)
            .where(UpstreamPrediction.stop_id == args.stop_id)
            .order_by(UpstreamPrediction.observed_at.desc())
            .limit(args.limit)
        ).all()
        emit(
            [
                {
                    "observed_at": row.observed_at,
                    "trip_id": row.trip_id,
                    "route_id": row.route_id,
                    "vehicle_id": row.vehicle_id,
                    "predicted_arrival": row.predicted_arrival,
                    "delay_seconds": row.delay_seconds,
                }
                for row in rows
            ]
        )


def command_rebuild_events(_: argparse.Namespace) -> None:
    # Replays the same conservative two-nearby-observation rule used by the collector.
    collector = Collector()
    created = 0
    with session_scope() as session:
        feed = active_feed(session)
        session.execute(delete(StopEvent).where(StopEvent.feed_version_id == feed.id))
        observations = session.scalars(
            select(VehicleObservation)
            .where(VehicleObservation.feed_version_id == feed.id)
            .order_by(VehicleObservation.vehicle_id, VehicleObservation.observed_at)
        ).all()
        for observation in observations:
            before = session.scalar(select(func.count(StopEvent.id)))
            collector._infer_stop_event(session, feed, observation)
            after = session.scalar(select(func.count(StopEvent.id)))
            created += int(after > before)
    emit({"stop_events_created": created})


def command_recompute_segments(_: argparse.Namespace) -> None:
    emit(rebuild_segment_statistics(settings.learning_timezone).__dict__)


def command_evaluate(_: argparse.Namespace) -> None:
    with session_scope() as session:
        emit(evaluate_predictions(session))


def command_rematch(_: argparse.Namespace) -> None:
    collector = Collector()
    updated = 0
    with session_scope() as session:
        feed = active_feed(session)
        rows = session.scalars(
            select(VehicleObservation)
            .where(VehicleObservation.feed_version_id == feed.id)
            .order_by(VehicleObservation.observed_at)
        ).all()
        for row in rows:
            if row.latitude is None or row.longitude is None:
                continue
            match = collector._match_vehicle(
                session,
                feed,
                {
                    "vehicle_id": row.vehicle_id,
                    "trip_id": row.trip_id,
                    "route_id": row.route_id,
                    "latitude": row.latitude,
                    "longitude": row.longitude,
                    "current_stop_sequence": row.current_stop_sequence,
                },
            )
            for key, value in match.items():
                setattr(row, key, value)
            updated += 1
    emit({"observations_rematched": updated})


def command_db_stats(_: argparse.Namespace) -> None:
    with session_scope() as session:
        counts = {
            table.name: session.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
        }
    db_bytes = (
        settings.sqlite_path.stat().st_size if settings.sqlite_path and settings.sqlite_path.exists() else 0
    )
    emit(
        {
            "database": str(settings.sqlite_path) if settings.sqlite_path else settings.database_url,
            "bytes": db_bytes,
            "rows": counts,
        }
    )


def command_prune(_: argparse.Namespace) -> None:
    with session_scope() as session:
        observation_cutoff = utcnow() - timedelta(days=settings.detailed_observation_retention_days)
        raw_cutoff = utcnow() - timedelta(days=settings.raw_snapshot_retention_days)
        observations = session.execute(
            delete(VehicleObservation).where(VehicleObservation.observed_at < observation_cutoff)
        ).rowcount
        # Snapshot metadata remains valuable for diagnostics and is referenced by
        # observations; only the short-lived raw protobuf blob is cleared.
        raw_payloads = session.execute(
            update(RealtimeSnapshot)
            .where(
                RealtimeSnapshot.observed_at < raw_cutoff,
                RealtimeSnapshot.raw_payload.is_not(None),
            )
            .values(raw_payload=None)
        ).rowcount
    emit(
        {
            "vehicle_observations_deleted": observations,
            "raw_snapshot_payloads_cleared": raw_payloads,
            "retention_days": {
                "observations": settings.detailed_observation_retention_days,
                "raw": settings.raw_snapshot_retention_days,
            },
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="UGA Bus data tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate").set_defaults(function=lambda _: create_schema())
    sub.add_parser("fetch-static").set_defaults(function=command_fetch_static)
    sub.add_parser("inspect-gtfs").set_defaults(function=command_inspect_gtfs)
    sub.add_parser("routes").set_defaults(function=command_routes)
    decoded = sub.add_parser("decode-realtime")
    decoded.add_argument("feed", choices=["vehicles", "trip-updates", "alerts"])
    decoded.add_argument("--limit", type=int, default=10)
    decoded.set_defaults(function=command_decode_realtime)
    vehicles = sub.add_parser("vehicles")
    vehicles.add_argument("--vehicle-id")
    vehicles.add_argument("--limit", type=int, default=50)
    vehicles.set_defaults(function=command_vehicles)
    trip = sub.add_parser("trip")
    trip.add_argument("trip_id")
    trip.set_defaults(function=command_trip)
    predictions = sub.add_parser("predictions")
    predictions.add_argument("stop_id")
    predictions.add_argument("--limit", type=int, default=100)
    predictions.set_defaults(function=command_predictions)
    sub.add_parser("rematch").set_defaults(function=command_rematch)
    sub.add_parser("rebuild-events").set_defaults(function=command_rebuild_events)
    sub.add_parser("recompute-segments").set_defaults(function=command_recompute_segments)
    sub.add_parser("evaluate").set_defaults(function=command_evaluate)
    sub.add_parser("db-stats").set_defaults(function=command_db_stats)
    sub.add_parser("prune").set_defaults(function=command_prune)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
