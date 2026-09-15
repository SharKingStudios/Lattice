from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median


@dataclass(frozen=True)
class EtaEstimate:
    arrival: datetime | None
    lower: datetime | None
    upper: datetime | None
    confidence: float
    source: str
    detail: str


def choose_eta(
    *,
    now: datetime,
    segment_seconds: Iterable[float] = (),
    geometric_seconds: float | None = None,
    scheduled_arrival: datetime | None = None,
    passio_arrival: datetime | None = None,
) -> EtaEstimate:
    samples = list(segment_seconds)
    if len(samples) >= 3:
        seconds = sum(samples)
        spread = max(30, abs(seconds - median(samples) * len(samples)) * 0.2)
        arrival = now + timedelta(seconds=seconds)
        return EtaEstimate(
            arrival,
            arrival - timedelta(seconds=spread),
            arrival + timedelta(seconds=spread),
            min(0.92, 0.45 + len(samples) / 40),
            "historical_segments",
            f"{len(samples)} segment samples",
        )
    if passio_arrival and passio_arrival > now:
        return EtaEstimate(
            passio_arrival,
            passio_arrival - timedelta(minutes=1),
            passio_arrival + timedelta(minutes=1),
            0.6,
            "passio_realtime",
            "upstream trip update",
        )
    if geometric_seconds and geometric_seconds > 0:
        arrival = now + timedelta(seconds=geometric_seconds)
        margin = max(60, geometric_seconds * 0.35)
        return EtaEstimate(
            arrival,
            arrival - timedelta(seconds=margin),
            arrival + timedelta(seconds=margin),
            0.25,
            "geometric_speed",
            "distance/speed fallback",
        )
    if scheduled_arrival and scheduled_arrival > now:
        return EtaEstimate(
            scheduled_arrival,
            scheduled_arrival - timedelta(minutes=3),
            scheduled_arrival + timedelta(minutes=5),
            0.2,
            "gtfs_schedule",
            "scheduled fallback",
        )
    return EtaEstimate(None, None, None, 0.0, "unknown", "no usable prediction")


def prediction_metrics(predictions: Iterable[tuple[datetime, datetime]]) -> dict[str, float | int | None]:
    errors = sorted(abs((predicted - actual).total_seconds()) for predicted, actual in predictions)
    if not errors:
        return {
            "sample_count": 0,
            "mae_seconds": None,
            "median_absolute_error_seconds": None,
            "p80_absolute_error_seconds": None,
            "p90_absolute_error_seconds": None,
            "bias_seconds": None,
        }
    signed = [(predicted - actual).total_seconds() for predicted, actual in predictions]
    percentile = lambda fraction: errors[min(len(errors) - 1, int((len(errors) - 1) * fraction))]
    return {
        "sample_count": len(errors),
        "mae_seconds": round(sum(errors) / len(errors), 1),
        "median_absolute_error_seconds": round(median(errors), 1),
        "p80_absolute_error_seconds": round(percentile(0.8), 1),
        "p90_absolute_error_seconds": round(percentile(0.9), 1),
        "bias_seconds": round(sum(signed) / len(signed), 1),
    }
