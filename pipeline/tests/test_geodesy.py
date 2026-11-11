"""UTM → WGS84 frente a valores de referencia calculados con pyproj (PROJ)."""

from __future__ import annotations

import pytest

from extrude.geodesy import to_wgs84, utm_zone

# (crs, x, y) → (lat, lon) de pyproj, Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
REFERENCE = [
    ("EPSG:25830", 440291.0, 4474254.0, 40.416769573, -3.703793295),  # Madrid
    ("EPSG:25830", 725800.0, 4372300.0, 39.470681493, -0.375058380),  # Valencia
    ("EPSG:32614", 483000.0, 2147000.0, 19.417211354, -99.161928030),  # Ciudad de México
    ("EPSG:32719", 346000.0, 6297000.0, -33.455378965, -70.657016441),  # Santiago de Chile
    ("EPSG:32654", 386000.0, 3950000.0, 35.687321631, 139.740114401),  # Tokio
    ("EPSG:26918", 583960.0, 4507523.0, 40.714349213, -74.005969527),  # Nueva York (NAD83)
]
MM_IN_DEG = 1e-8  # ~1 mm


@pytest.mark.parametrize(("crs", "x", "y", "lat", "lon"), REFERENCE)
def test_utm_matches_proj(crs: str, x: float, y: float, lat: float, lon: float) -> None:
    result = to_wgs84(crs, x, y)
    assert result is not None
    assert result[0] == pytest.approx(lat, abs=MM_IN_DEG)
    assert result[1] == pytest.approx(lon, abs=MM_IN_DEG)


@pytest.mark.parametrize(
    ("crs", "zone"),
    [
        ("EPSG:25830", (30, False)),
        ("epsg: 32719", (19, True)),
        ("EPSG:32601", (1, False)),
        ("EPSG:4326", None),  # geográfico, no UTM
        ("EPSG:3857", None),
        ("ETRS89", None),
        (None, None),
    ],
)
def test_utm_zone_from_epsg(crs: str | None, zone: tuple[int, bool] | None) -> None:
    assert utm_zone(crs) == zone


def test_unknown_crs_without_pyproj_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_pyproj(name: str, *args: object, **kwargs: object) -> object:
        if name == "pyproj":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_pyproj)
    assert to_wgs84("EPSG:3857", 0.0, 0.0) is None
    assert to_wgs84(None, 440291.0, 4474254.0) is None


@pytest.mark.parametrize(
    ("x", "y"),
    [
        (1e12, 4140000),
        (float("nan"), 0),
        (500000, float("inf")),
        (-1, 4140000),
        (500000, -1),
        (500000, 1e12),
    ],
)
def test_invalid_utm_coordinates_have_no_geolocation(x: float, y: float) -> None:
    assert to_wgs84("EPSG:25830", x, y) is None
