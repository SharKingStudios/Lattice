from datetime import UTC, datetime, timedelta

import pytest
from uga_bus.eta import choose_eta, prediction_metrics
from uga_bus.gtfs import parse_gtfs_time
from uga_bus.learning import _valid_segment, service_bucket
from uga_bus.db import StopEvent
from uga_bus.matching import project_on_polyline


def test_gtfs_time_supports_after_midnight():
    assert parse_gtfs_time("25:10:00") == 90_600
    with pytest.raises(ValueError):
        parse_gtfs_time("08:61:00")


def test_projection_has_progress_and_cross_track_distance():
    match = project_on_polyline(33.95, -83.38, [(33.95, -83.381), (33.95, -83.379)])
    assert match is not None
    assert 80 < match.progress_meters < 120
    assert match.cross_track_meters < 1


def test_eta_fallback_order_is_explicit():
    now = datetime.now(UTC)
    historical = choose_eta(
        now=now, segment_seconds=[60, 65, 55], passio_arrival=now + timedelta(minutes=9)
    )
    assert historical.source == "uga_estimation"
    fallback = choose_eta(now=now, passio_arrival=now + timedelta(minutes=9))
    assert fallback.source == "passio_realtime"


def test_realtime_eta_beats_straight_line_fallback():
    now = datetime.now(UTC)
    estimate = choose_eta(
        now=now,
        geometric_seconds=60,
        passio_arrival=now + timedelta(minutes=9),
    )
    assert estimate.source == "passio_realtime"


def test_prediction_metrics():
    now = datetime.now(UTC)
    metrics = prediction_metrics(
        [(now + timedelta(seconds=30), now), (now - timedelta(seconds=10), now)]
    )
    assert metrics["sample_count"] == 2
    assert metrics["mae_seconds"] == 20


def test_learning_uses_eastern_operating_time_buckets():
    # 16:00 UTC is noon in Athens during daylight saving time.
    assert service_bucket(datetime(2026, 9, 14, 16, tzinfo=UTC), "America/New_York") == "weekday-12"
    assert service_bucket(datetime(2026, 9, 13, 16, tzinfo=UTC), "America/New_York") == "weekend"


def test_learning_uses_arrival_to_arrival_time_when_departures_are_missing():
    started = datetime(2026, 9, 14, 16, tzinfo=UTC)
    previous = StopEvent(
        vehicle_id="bus-1", route_id="orbit", stop_id="a", stop_sequence=4,
        arrival_at=started, confidence=0.8, inference_method="test",
    )
    current = StopEvent(
        vehicle_id="bus-1", route_id="orbit", stop_id="b", stop_sequence=5,
        arrival_at=started + timedelta(seconds=95), confidence=0.8, inference_method="test",
    )
    assert _valid_segment(previous, current) == 95
