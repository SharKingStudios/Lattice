from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from .db import FeedVersion, SegmentStatistic, StopEvent, as_utc, session_scope, utcnow


@dataclass(frozen=True)
class LearningSummary:
    segments: int
    samples: int
    usable_segments: int
    refreshed_at: datetime


def service_bucket(when: datetime, timezone_name: str) -> str:
    """Group comparable trips by local operating time, not the server's clock."""
    local = as_utc(when).astimezone(ZoneInfo(timezone_name))
    if local.weekday() >= 5:
        return "weekend"
    return f"weekday-{local.hour // 3 * 3:02d}"


def _valid_segment(previous: StopEvent, current: StopEvent) -> float | None:
    if (
        not previous.vehicle_id
        or previous.vehicle_id != current.vehicle_id
        or not previous.route_id
        or previous.route_id != current.route_id
        or previous.stop_sequence is None
        or current.stop_sequence is None
        or current.stop_sequence <= previous.stop_sequence
        or previous.confidence < 0.5
        or current.confidence < 0.5
    ):
        return None
    # Arrival-to-arrival timing includes the usual dwell at the origin, which
    # is useful when a rider's bus has not yet cleared that stop.
    seconds = (as_utc(current.arrival_at) - as_utc(previous.arrival_at)).total_seconds()
    # Reject duplicated observations, layovers, and implausible cross-route jumps.
    return seconds if 8 <= seconds <= 1_800 else None


def rebuild_segment_statistics(timezone_name: str) -> LearningSummary:
    """Rebuild learned travel-time segments from the retained, replayable stop events.

    A complete rebuild is deliberate: it lets corrected departures and newly inferred
    stop events improve the model without keeping fragile in-memory state. The input
    is small relative to the live feed and runs outside the async event loop.
    """
    records: defaultdict[tuple[int | None, str, str, str, str], list[float]] = defaultdict(list)
    with session_scope() as session:
        active_feed = session.scalar(select(FeedVersion).where(FeedVersion.is_active.is_(True)))
        events = session.scalars(
            select(StopEvent)
            .where(StopEvent.confidence >= 0.5)
            .order_by(StopEvent.vehicle_id, StopEvent.arrival_at)
        ).all()
        previous_by_vehicle: dict[str, StopEvent] = {}
        for event in events:
            if not event.vehicle_id:
                continue
            previous = previous_by_vehicle.get(event.vehicle_id)
            if previous:
                seconds = _valid_segment(previous, event)
                if seconds is not None and event.route_id:
                    records[
                        (
                            active_feed.id if active_feed else event.feed_version_id,
                            event.route_id,
                            previous.stop_id,
                            event.stop_id,
                            service_bucket(event.arrival_at, timezone_name),
                        )
                    ].append(seconds)
            previous_by_vehicle[event.vehicle_id] = event

        session.execute(delete(SegmentStatistic))
        for (feed_id, route_id, origin, target, bucket), values in records.items():
            ordered = sorted(values)
            session.add(
                SegmentStatistic(
                    feed_version_id=feed_id,
                    route_id=route_id,
                    from_stop_id=origin,
                    to_stop_id=target,
                    service_bucket=bucket,
                    sample_count=len(values),
                    mean_seconds=sum(values) / len(values),
                    p80_seconds=ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.8))],
                )
            )

    return LearningSummary(
        segments=len(records),
        samples=sum(len(values) for values in records.values()),
        usable_segments=sum(1 for values in records.values() if len(values) >= 3),
        refreshed_at=utcnow(),
    )
