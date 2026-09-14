from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import cos, hypot, radians


@dataclass(frozen=True)
class Projection:
    progress_meters: float
    cross_track_meters: float


def project_on_polyline(
    latitude: float, longitude: float, points: Iterable[tuple[float, float]]
) -> Projection | None:
    """Project lat/lon point onto a route polyline using a locally accurate planar transform."""
    route = list(points)
    if len(route) < 2:
        return None
    latitude_scale = 111_320.0
    longitude_scale = 111_320.0 * cos(radians(latitude))
    best_distance = float("inf")
    best_progress = 0.0
    cumulative = 0.0
    for (a_lat, a_lon), (b_lat, b_lon) in zip(route, route[1:]):
        ax, ay = (a_lon - longitude) * longitude_scale, (a_lat - latitude) * latitude_scale
        bx, by = (b_lon - longitude) * longitude_scale, (b_lat - latitude) * latitude_scale
        dx, dy = bx - ax, by - ay
        length_squared = dx * dx + dy * dy
        segment_length = hypot(dx, dy)
        t = 0.0 if not length_squared else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_squared))
        distance = hypot(ax + t * dx, ay + t * dy)
        if distance < best_distance:
            best_distance, best_progress = distance, cumulative + t * segment_length
        cumulative += segment_length
    return Projection(progress_meters=best_progress, cross_track_meters=best_distance)


def match_confidence(cross_track_meters: float, has_trip: bool, continuity_penalty: float = 0.0) -> float:
    geography = max(0.0, 1 - cross_track_meters / 250)
    return round(max(0.0, min(1.0, geography * (0.8 if has_trip else 0.55) - continuity_penalty)), 3)
