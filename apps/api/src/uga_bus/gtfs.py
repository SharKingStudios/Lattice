from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections import Counter
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import (
    Agency,
    Calendar,
    CalendarDate,
    FeedVersion,
    Frequency,
    Route,
    ShapePoint,
    Stop,
    StopTime,
    Trip,
    json_dumps,
)

OPTIONAL_TABLES = {"agency", "calendar", "calendar_dates", "frequencies", "feed_info", "shapes"}
REQUIRED_TABLES = {"stops", "routes", "trips", "stop_times"}


def clean(row: dict[str, str], name: str) -> str | None:
    value = row.get(name, "").strip()
    return value or None


def integer(row: dict[str, str], name: str) -> int | None:
    value = clean(row, name)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def number(row: dict[str, str], name: str) -> float | None:
    value = clean(row, name)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def rows(archive: zipfile.ZipFile, table: str) -> list[dict[str, str]]:
    filename = f"{table}.txt"
    if filename not in archive.namelist():
        return []
    text = archive.read(filename).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def normalize_gtfs_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def parse_gtfs_time(value: str) -> int:
    """Parse a GTFS time, including service after midnight (for example 25:10:00)."""
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    if hours < 0 or minutes not in range(60) or seconds not in range(60):
        raise ValueError(f"invalid GTFS time: {value}")
    return hours * 3600 + minutes * 60 + seconds


def is_service_active(session: Session, feed_id: int, service_id: str, service_date: date) -> bool:
    iso = service_date.isoformat()
    exception = session.scalar(
        select(CalendarDate).where(
            CalendarDate.feed_version_id == feed_id,
            CalendarDate.service_id == service_id,
            CalendarDate.date == iso,
        )
    )
    if exception:
        return exception.exception_type == 1
    calendar = session.scalar(
        select(Calendar).where(
            Calendar.feed_version_id == feed_id,
            Calendar.service_id == service_id,
        )
    )
    if not calendar or not (calendar.start_date <= iso <= calendar.end_date):
        return False
    return bool(
        (
            calendar.monday,
            calendar.tuesday,
            calendar.wednesday,
            calendar.thursday,
            calendar.friday,
            calendar.saturday,
            calendar.sunday,
        )[service_date.weekday()]
    )


def validate_tables(table_rows: dict[str, list[dict[str, str]]]) -> dict[str, object]:
    warnings: list[str] = []
    names = set(table_rows)
    missing = REQUIRED_TABLES - names
    if missing:
        warnings.append(f"missing required GTFS tables: {', '.join(sorted(missing))}")
    route_ids = {r.get("route_id", "") for r in table_rows.get("routes", [])}
    stop_ids = {r.get("stop_id", "") for r in table_rows.get("stops", [])}
    trip_ids = {r.get("trip_id", "") for r in table_rows.get("trips", [])}
    shape_ids = {r.get("shape_id", "") for r in table_rows.get("shapes", [])}
    service_ids = {r.get("service_id", "") for r in table_rows.get("calendar", [])} | {
        r.get("service_id", "") for r in table_rows.get("calendar_dates", [])
    }
    counts = Counter()
    for trip in table_rows.get("trips", []):
        if trip.get("route_id") not in route_ids:
            counts["trip_route"] += 1
        if trip.get("shape_id") and trip["shape_id"] not in shape_ids:
            counts["trip_shape"] += 1
        if service_ids and trip.get("service_id") not in service_ids:
            counts["trip_service"] += 1
    for st in table_rows.get("stop_times", []):
        if st.get("trip_id") not in trip_ids:
            counts["stop_time_trip"] += 1
        if st.get("stop_id") not in stop_ids:
            counts["stop_time_stop"] += 1
        try:
            # GTFS allows either time to be omitted in conditional stop-time cases.
            if st.get("arrival_time"):
                parse_gtfs_time(st["arrival_time"])
            if st.get("departure_time"):
                parse_gtfs_time(st["departure_time"])
        except ValueError:
            counts["invalid_time"] += 1
    for key, count in counts.items():
        warnings.append(f"{count} referential/time validation issue(s): {key}")
    return {"valid": not missing, "warnings": warnings, "integrity_counts": dict(counts)}


def ingest_gtfs(
    session: Session, payload: bytes, source_url: str, headers: dict[str, str]
) -> tuple[FeedVersion, bool]:
    checksum = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        available = {name.removesuffix(".txt") for name in archive.namelist() if name.endswith(".txt")}
        table_rows = {table: rows(archive, table) for table in available}
    validation = validate_tables(table_rows)
    row_counts = {key: len(value) for key, value in table_rows.items()}
    existing = session.scalar(select(FeedVersion).where(FeedVersion.checksum == checksum))
    if existing:
        # The bytes are unchanged, but validation rules may have improved since
        # the prior check; refresh diagnostics without replacing the feed version.
        existing.validation_json = json_dumps(validation)
        existing.row_counts_json = json_dumps(row_counts)
        if not existing.is_active:
            session.execute(update(FeedVersion).values(is_active=False))
            existing.is_active = True
        return existing, False
    feed_info = table_rows.get("feed_info", [{}])[0] if table_rows.get("feed_info") else {}
    all_dates = [normalize_gtfs_date(r.get("start_date")) for r in table_rows.get("calendar", [])] + [
        normalize_gtfs_date(r.get("date")) for r in table_rows.get("calendar_dates", [])
    ]
    all_end_dates = [normalize_gtfs_date(r.get("end_date")) for r in table_rows.get("calendar", [])] + [
        normalize_gtfs_date(r.get("date")) for r in table_rows.get("calendar_dates", [])
    ]
    dates = [d for d in all_dates if d]
    end_dates = [d for d in all_end_dates if d]
    session.execute(update(FeedVersion).values(is_active=False))
    version = FeedVersion(
        checksum=checksum,
        source_url=source_url,
        http_etag=headers.get("etag"),
        http_last_modified=headers.get("last-modified"),
        feed_publisher_name=clean(feed_info, "feed_publisher_name"),
        feed_version=clean(feed_info, "feed_version"),
        start_date=min(dates) if dates else None,
        end_date=max(end_dates) if end_dates else None,
        row_counts_json=json_dumps(row_counts),
        validation_json=json_dumps(validation),
        is_active=True,
    )
    session.add(version)
    session.flush()
    feed_id = version.id
    session.add_all(
        Agency(
            feed_version_id=feed_id,
            agency_id=clean(r, "agency_id") or "",
            name=clean(r, "agency_name") or "Unnamed agency",
            url=clean(r, "agency_url"),
            timezone=clean(r, "agency_timezone"),
        )
        for r in table_rows.get("agency", [])
    )
    session.add_all(
        Route(
            feed_version_id=feed_id,
            route_id=clean(r, "route_id") or "",
            agency_id=clean(r, "agency_id"),
            short_name=clean(r, "route_short_name"),
            long_name=clean(r, "route_long_name"),
            description=clean(r, "route_desc"),
            route_type=integer(r, "route_type"),
            color=clean(r, "route_color"),
            text_color=clean(r, "route_text_color"),
            sort_order=integer(r, "route_sort_order"),
        )
        for r in table_rows.get("routes", [])
    )
    session.add_all(
        Stop(
            feed_version_id=feed_id,
            stop_id=clean(r, "stop_id") or "",
            code=clean(r, "stop_code"),
            name=clean(r, "stop_name") or "Unnamed stop",
            description=clean(r, "stop_desc"),
            latitude=number(r, "stop_lat"),
            longitude=number(r, "stop_lon"),
            zone_id=clean(r, "zone_id"),
            location_type=integer(r, "location_type"),
            parent_station=clean(r, "parent_station"),
            wheelchair_boarding=integer(r, "wheelchair_boarding"),
        )
        for r in table_rows.get("stops", [])
    )
    session.add_all(
        Trip(
            feed_version_id=feed_id,
            route_id=clean(r, "route_id") or "",
            service_id=clean(r, "service_id") or "",
            trip_id=clean(r, "trip_id") or "",
            headsign=clean(r, "trip_headsign"),
            short_name=clean(r, "trip_short_name"),
            direction_id=integer(r, "direction_id"),
            block_id=clean(r, "block_id"),
            shape_id=clean(r, "shape_id"),
            wheelchair_accessible=integer(r, "wheelchair_accessible"),
            bikes_allowed=integer(r, "bikes_allowed"),
        )
        for r in table_rows.get("trips", [])
    )
    session.add_all(
        StopTime(
            feed_version_id=feed_id,
            trip_id=clean(r, "trip_id") or "",
            arrival_time=clean(r, "arrival_time"),
            departure_time=clean(r, "departure_time"),
            stop_id=clean(r, "stop_id") or "",
            stop_sequence=integer(r, "stop_sequence") or 0,
            headsign=clean(r, "stop_headsign"),
            pickup_type=integer(r, "pickup_type"),
            drop_off_type=integer(r, "drop_off_type"),
            timepoint=integer(r, "timepoint"),
        )
        for r in table_rows.get("stop_times", [])
    )
    session.add_all(
        ShapePoint(
            feed_version_id=feed_id,
            shape_id=clean(r, "shape_id") or "",
            latitude=number(r, "shape_pt_lat") or 0,
            longitude=number(r, "shape_pt_lon") or 0,
            sequence=integer(r, "shape_pt_sequence") or 0,
            distance_traveled=number(r, "shape_dist_traveled"),
        )
        for r in table_rows.get("shapes", [])
    )
    session.add_all(
        Calendar(
            feed_version_id=feed_id,
            service_id=clean(r, "service_id") or "",
            monday=integer(r, "monday") or 0,
            tuesday=integer(r, "tuesday") or 0,
            wednesday=integer(r, "wednesday") or 0,
            thursday=integer(r, "thursday") or 0,
            friday=integer(r, "friday") or 0,
            saturday=integer(r, "saturday") or 0,
            sunday=integer(r, "sunday") or 0,
            start_date=normalize_gtfs_date(r.get("start_date")) or "",
            end_date=normalize_gtfs_date(r.get("end_date")) or "",
        )
        for r in table_rows.get("calendar", [])
    )
    session.add_all(
        CalendarDate(
            feed_version_id=feed_id,
            service_id=clean(r, "service_id") or "",
            date=normalize_gtfs_date(r.get("date")) or "",
            exception_type=integer(r, "exception_type") or 0,
        )
        for r in table_rows.get("calendar_dates", [])
    )
    session.add_all(
        Frequency(
            feed_version_id=feed_id,
            trip_id=clean(r, "trip_id") or "",
            start_time=clean(r, "start_time") or "",
            end_time=clean(r, "end_time") or "",
            headway_secs=integer(r, "headway_secs") or 0,
            exact_times=integer(r, "exact_times"),
        )
        for r in table_rows.get("frequencies", [])
    )
    return version, True
