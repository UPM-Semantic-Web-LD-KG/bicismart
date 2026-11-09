"""Distancias en línea recta entre coordenadas WGS84."""

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia haversine en metros."""
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def nearest(items: list[dict], lat: float, lon: float, k: int) -> list[dict]:
    """Los `k` elementos (con claves lat/lon) más cercanos a un punto."""
    return sorted(items, key=lambda item: distance_m(lat, lon, item["lat"], item["lon"]))[:k]


def centroid(items: list[dict]) -> tuple[float, float]:
    return (sum(i["lat"] for i in items) / len(items), sum(i["lon"] for i in items) / len(items))
