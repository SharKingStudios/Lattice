from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite does not round-trip tzinfo; interpret stored datetimes as UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class FeedVersion(Base):
    __tablename__ = "gtfs_feed_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_url: Mapped[str] = mapped_column(Text)
    http_etag: Mapped[str | None] = mapped_column(String(255))
    http_last_modified: Mapped[str | None] = mapped_column(String(255))
    feed_publisher_name: Mapped[str | None] = mapped_column(String(255))
    feed_version: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    row_counts_json: Mapped[str] = mapped_column(Text, default="{}")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Agency(Base):
    __tablename__ = "agencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    agency_id: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str | None] = mapped_column(String(64))


class Route(Base):
    __tablename__ = "routes"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    route_id: Mapped[str] = mapped_column(String(255), index=True)
    agency_id: Mapped[str | None] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(255))
    long_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    route_type: Mapped[int | None] = mapped_column(Integer)
    color: Mapped[str | None] = mapped_column(String(12))
    text_color: Mapped[str | None] = mapped_column(String(12))
    sort_order: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_routes_feed_route", "feed_version_id", "route_id", unique=True),)


class Stop(Base):
    __tablename__ = "stops"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    stop_id: Mapped[str] = mapped_column(String(255), index=True)
    code: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(512), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    zone_id: Mapped[str | None] = mapped_column(String(255))
    location_type: Mapped[int | None] = mapped_column(Integer)
    parent_station: Mapped[str | None] = mapped_column(String(255))
    wheelchair_boarding: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_stops_feed_stop", "feed_version_id", "stop_id", unique=True),)


class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    route_id: Mapped[str] = mapped_column(String(255), index=True)
    service_id: Mapped[str] = mapped_column(String(255), index=True)
    trip_id: Mapped[str] = mapped_column(String(255), index=True)
    headsign: Mapped[str | None] = mapped_column(String(512))
    short_name: Mapped[str | None] = mapped_column(String(255))
    direction_id: Mapped[int | None] = mapped_column(Integer)
    block_id: Mapped[str | None] = mapped_column(String(255))
    shape_id: Mapped[str | None] = mapped_column(String(255), index=True)
    wheelchair_accessible: Mapped[int | None] = mapped_column(Integer)
    bikes_allowed: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_trips_feed_trip", "feed_version_id", "trip_id", unique=True),)


class StopTime(Base):
    __tablename__ = "stop_times"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    trip_id: Mapped[str] = mapped_column(String(255), index=True)
    arrival_time: Mapped[str | None] = mapped_column(String(12))
    departure_time: Mapped[str | None] = mapped_column(String(12))
    stop_id: Mapped[str] = mapped_column(String(255), index=True)
    stop_sequence: Mapped[int] = mapped_column(Integer)
    headsign: Mapped[str | None] = mapped_column(String(512))
    pickup_type: Mapped[int | None] = mapped_column(Integer)
    drop_off_type: Mapped[int | None] = mapped_column(Integer)
    timepoint: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        Index("ix_stop_times_feed_trip_sequence", "feed_version_id", "trip_id", "stop_sequence", unique=True),
    )


class ShapePoint(Base):
    __tablename__ = "shape_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    shape_id: Mapped[str] = mapped_column(String(255), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    sequence: Mapped[int] = mapped_column(Integer)
    distance_traveled: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (
        Index("ix_shape_points_feed_shape_sequence", "feed_version_id", "shape_id", "sequence", unique=True),
    )


class Calendar(Base):
    __tablename__ = "calendar"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    service_id: Mapped[str] = mapped_column(String(255), index=True)
    monday: Mapped[int] = mapped_column(Integer)
    tuesday: Mapped[int] = mapped_column(Integer)
    wednesday: Mapped[int] = mapped_column(Integer)
    thursday: Mapped[int] = mapped_column(Integer)
    friday: Mapped[int] = mapped_column(Integer)
    saturday: Mapped[int] = mapped_column(Integer)
    sunday: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    __table_args__ = (Index("ix_calendar_feed_service", "feed_version_id", "service_id", unique=True),)


class CalendarDate(Base):
    __tablename__ = "calendar_dates"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    service_id: Mapped[str] = mapped_column(String(255), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    exception_type: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        Index("ix_calendar_dates_feed_service_date", "feed_version_id", "service_id", "date", unique=True),
    )


class Frequency(Base):
    __tablename__ = "frequencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    trip_id: Mapped[str] = mapped_column(String(255), index=True)
    start_time: Mapped[str] = mapped_column(String(12))
    end_time: Mapped[str] = mapped_column(String(12))
    headway_secs: Mapped[int] = mapped_column(Integer)
    exact_times: Mapped[int | None] = mapped_column(Integer)


class RealtimeSnapshot(Base):
    __tablename__ = "realtime_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_type: Mapped[str] = mapped_column(String(32), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    download_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    download_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    payload_size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64))
    feed_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    parsed_entities: Mapped[int] = mapped_column(Integer, default=0)
    parse_error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    identical_to_previous: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_payload: Mapped[bytes | None] = mapped_column()


class VehicleObservation(Base):
    __tablename__ = "vehicle_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("realtime_snapshots.id"), index=True)
    feed_version_id: Mapped[int | None] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    feed_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entity_id: Mapped[str | None] = mapped_column(String(255))
    vehicle_id: Mapped[str | None] = mapped_column(String(255), index=True)
    trip_id: Mapped[str | None] = mapped_column(String(255), index=True)
    route_id: Mapped[str | None] = mapped_column(String(255), index=True)
    direction_id: Mapped[int | None] = mapped_column(Integer)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    bearing: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    current_stop_sequence: Mapped[int | None] = mapped_column(Integer)
    stop_id: Mapped[str | None] = mapped_column(String(255))
    current_status: Mapped[int | None] = mapped_column(Integer)
    congestion_level: Mapped[int | None] = mapped_column(Integer)
    occupancy_status: Mapped[int | None] = mapped_column(Integer)
    matched_trip_id: Mapped[str | None] = mapped_column(String(255), index=True)
    matched_shape_id: Mapped[str | None] = mapped_column(String(255))
    progress_meters: Mapped[float | None] = mapped_column(Float)
    cross_track_meters: Mapped[float | None] = mapped_column(Float)
    previous_stop_id: Mapped[str | None] = mapped_column(String(255))
    next_stop_id: Mapped[str | None] = mapped_column(String(255))
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_method: Mapped[str | None] = mapped_column(String(64))
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class UpstreamPrediction(Base):
    __tablename__ = "upstream_predictions"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("realtime_snapshots.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    feed_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entity_id: Mapped[str | None] = mapped_column(String(255))
    vehicle_id: Mapped[str | None] = mapped_column(String(255), index=True)
    trip_id: Mapped[str | None] = mapped_column(String(255), index=True)
    route_id: Mapped[str | None] = mapped_column(String(255), index=True)
    stop_id: Mapped[str] = mapped_column(String(255), index=True)
    stop_sequence: Mapped[int | None] = mapped_column(Integer)
    predicted_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    predicted_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delay_seconds: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(64), default="passio_gtfs_rt")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class ServiceAlert(Base):
    __tablename__ = "service_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("realtime_snapshots.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(255), index=True)
    cause: Mapped[int | None] = mapped_column(Integer)
    effect: Mapped[int | None] = mapped_column(Integer)
    header: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    active_periods_json: Mapped[str] = mapped_column(Text, default="[]")
    informed_entities_json: Mapped[str] = mapped_column(Text, default="[]")


class StopEvent(Base):
    __tablename__ = "stop_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int | None] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    vehicle_id: Mapped[str | None] = mapped_column(String(255), index=True)
    trip_id: Mapped[str | None] = mapped_column(String(255), index=True)
    route_id: Mapped[str | None] = mapped_column(String(255), index=True)
    stop_id: Mapped[str] = mapped_column(String(255), index=True)
    stop_sequence: Mapped[int | None] = mapped_column(Integer)
    arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dwell_seconds: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    inference_method: Mapped[str] = mapped_column(String(64))


class SegmentStatistic(Base):
    __tablename__ = "segment_statistics"
    id: Mapped[int] = mapped_column(primary_key=True)
    feed_version_id: Mapped[int | None] = mapped_column(ForeignKey("gtfs_feed_versions.id"), index=True)
    route_id: Mapped[str] = mapped_column(String(255), index=True)
    from_stop_id: Mapped[str] = mapped_column(String(255))
    to_stop_id: Mapped[str] = mapped_column(String(255))
    service_bucket: Mapped[str] = mapped_column(String(64))
    sample_count: Mapped[int] = mapped_column(Integer)
    mean_seconds: Mapped[float] = mapped_column(Float)
    p80_seconds: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        Index(
            "ix_segments_lookup",
            "feed_version_id",
            "route_id",
            "from_stop_id",
            "to_stop_id",
            "service_bucket",
            unique=True,
        ),
    )


class EtaPrediction(Base):
    __tablename__ = "eta_predictions"
    id: Mapped[int] = mapped_column(primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    vehicle_id: Mapped[str | None] = mapped_column(String(255), index=True)
    trip_id: Mapped[str | None] = mapped_column(String(255), index=True)
    route_id: Mapped[str | None] = mapped_column(String(255), index=True)
    stop_id: Mapped[str] = mapped_column(String(255), index=True)
    estimated_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lower_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upper_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64))
    details_json: Mapped[str] = mapped_column(Text, default="{}")


engine = create_engine(
    settings.database_url,
    future=True,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def create_schema() -> None:
    if settings.sqlite_path:
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
