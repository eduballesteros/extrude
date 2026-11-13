"""Coordenadas proyectadas → latitud/longitud WGS84, para situar el sol del visor.

Sin dependencias: proyección UTM inversa (Transverse Mercator, serie de Krüger de orden 6,
precisión submilimétrica dentro de la zona) para los EPSG UTM habituales. Datums tratados como
WGS84: ETRS89 y NAD83 difieren menos de 1 o 2 m, irrelevante para la posición del sol.
Si `pyproj` está instalado se usa para cualquier otro CRS.
"""

from __future__ import annotations

import math
import re

WGS84_A = 6_378_137.0
WGS84_F = 1 / 298.257223563
UTM_K0 = 0.9996
UTM_FALSE_EASTING = 500_000.0
UTM_FALSE_NORTHING_SOUTH = 10_000_000.0

# (primer código, último código, hemisferio sur) → zona = código - primer código + 1
_UTM_EPSG: tuple[tuple[int, int, bool], ...] = (
    (32601, 32660, False),  # WGS 84 / UTM norte
    (32701, 32760, True),  # WGS 84 / UTM sur
    (25828, 25838, False),  # ETRS89 / UTM zonas 28 a 38 (los únicos que existen)
    (26901, 26923, False),  # NAD83 / UTM (Norteamérica)
)
_EPSG = re.compile(r"^\s*EPSG\s*:\s*(\d+)\s*$", re.IGNORECASE)


def _krueger() -> tuple[float, tuple[float, ...], tuple[float, ...]]:
    n = WGS84_F / (2 - WGS84_F)
    n2, n3, n4, n5, n6 = n**2, n**3, n**4, n**5, n**6
    rectifying_a = WGS84_A / (1 + n) * (1 + n2 / 4 + n4 / 64 + n6 / 256)
    beta = (
        n / 2 - 2 * n2 / 3 + 37 * n3 / 96 - n4 / 360 - 81 * n5 / 512 + 96199 * n6 / 604800,
        n2 / 48 + n3 / 15 - 437 * n4 / 1440 + 46 * n5 / 105 - 1118711 * n6 / 3870720,
        17 * n3 / 480 - 37 * n4 / 840 - 209 * n5 / 4480 + 5569 * n6 / 90720,
        4397 * n4 / 161280 - 11 * n5 / 504 - 830251 * n6 / 7257600,
        4583 * n5 / 161280 - 108847 * n6 / 3991680,
        20648693 * n6 / 638668800,
    )
    delta = (
        2 * n - 2 * n2 / 3 - 2 * n3 + 116 * n4 / 45 + 26 * n5 / 45 - 2854 * n6 / 675,
        7 * n2 / 3 - 8 * n3 / 5 - 227 * n4 / 45 + 2704 * n5 / 315 + 2323 * n6 / 945,
        56 * n3 / 15 - 136 * n4 / 35 - 1262 * n5 / 105 + 73814 * n6 / 2835,
        4279 * n4 / 630 - 332 * n5 / 35 - 399572 * n6 / 14175,
        4174 * n5 / 315 - 144838 * n6 / 6237,
        601676 * n6 / 22275,
    )
    return rectifying_a, beta, delta


_A, _BETA, _DELTA = _krueger()


def utm_zone(crs: str | None) -> tuple[int, bool] | None:
    """(zona, hemisferio sur) para un "EPSG:xxxx" UTM conocido; None en otro caso."""
    match = _EPSG.match(crs or "")
    if not match:
        return None
    code = int(match.group(1))
    for first, last, south in _UTM_EPSG:
        if first <= code <= last:
            return code % 100, south  # el número de zona son las dos últimas cifras
    return None


def utm_to_wgs84(easting: float, northing: float, zone: int, south: bool) -> tuple[float, float]:
    """(lat, lon) en grados a partir de coordenadas UTM en metros."""
    northing -= UTM_FALSE_NORTHING_SOUTH if south else 0.0
    xi = northing / (UTM_K0 * _A)
    eta = (easting - UTM_FALSE_EASTING) / (UTM_K0 * _A)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, start=1))
    central_meridian = math.radians(zone * 6 - 183)
    lon = central_meridian + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lat), (math.degrees(lon) + 180) % 360 - 180


def to_wgs84(crs: str | None, x: float, y: float) -> tuple[float, float] | None:
    """(lat, lon) del punto (x, y) en metros del CRS, o None si no se sabe reproyectar."""
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    zone = utm_zone(crs)
    if zone is not None:
        # Dominio amplio de UTM, incluidas las extensiones de zonas vecinas.
        if not (0 <= x <= 1_000_000 and 0 <= y <= UTM_FALSE_NORTHING_SOUTH):
            return None
        try:
            lat, lon = utm_to_wgs84(x, y, *zone)
        except (ValueError, OverflowError):
            return None
        if not -80 <= lat <= 84:
            return None
    elif crs:
        try:
            from pyproj import Transformer  # type: ignore[import-not-found,unused-ignore]
        except ImportError:
            return None
        try:
            lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(x, y)
        except Exception:  # CRS desconocido o no proyectado
            return None
    else:
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90):
        return None
    return lat, lon
