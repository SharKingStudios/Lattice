from datetime import UTC, datetime, timedelta

import pytest
from uga_bus.eta import choose_eta, prediction_metrics
from uga_bus.gtfs import parse_gtfs_time
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
    assert historical.source == "historical_segments"
    fallback = choose_eta(now=now, passio_arrival=now + timedelta(minutes=9))
    assert fallback.source == "passio_fallback"


def test_prediction_metrics():
    now = datetime.now(UTC)
    metrics = prediction_metrics(
        [(now + timedelta(seconds=30), now), (now - timedelta(seconds=10), now)]
    )
    assert metrics["sample_count"] == 2
    assert metrics["mae_seconds"] == 20
