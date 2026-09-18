"""Archive-mode SPC outlook fetching, including the pre-2020 shapefile fallback.

SPC only started publishing day1/2 outlooks as .lyr.geojson on 2020-01-01;
older dates are only in the archive as one shapefile zip per cycle bundling
every product's .shp/.dbf together. Confirmed live against a real archive
day (2019-06-08): the geojson URLs 404 for every candidate, the shapefile
zip has real data.
"""
import io
import struct
import zipfile

import json

from archive.fetchers.hazard_archive_fetcher import (
    _archive_spc_shapefile_zip_url,
    _normalize_archive_spc_geojson,
    _parse_outlook_shapefile_zip,
)


def _build_shp(polygons):
    """Minimal ESRI Shapefile (.shp) with Polygon (type 5) records --
    the same subset _parse_shp reads. Each polygon is one ring: a list of
    (x, y) tuples."""
    body = b""
    for i, ring in enumerate(polygons, start=1):
        pts = ring
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        content = struct.pack("<i", 5)  # shape type: polygon
        content += struct.pack("<dddd", min(xs), min(ys), max(xs), max(ys))
        content += struct.pack("<ii", 1, len(pts))  # 1 part, N points
        content += struct.pack("<i", 0)  # part starts at point 0
        for x, y in pts:
            content += struct.pack("<dd", x, y)
        header = struct.pack(">ii", i, len(content) // 2)
        body += header + content
    return b"\x00" * 100 + body  # 100-byte file header, content ignored by the parser


def _build_dbf(records):
    """Minimal dBASE III (.dbf) with a single numeric DN field -- the same
    subset _parse_dbf reads."""
    field_len = 10
    header_bytes = 32 + 32 + 1  # base header + one field descriptor + terminator
    record_bytes = 1 + field_len  # deletion flag + DN field
    header = struct.pack("<BBBBIHH20x", 3, 0, 0, 0, len(records), header_bytes, record_bytes)
    field = b"DN".ljust(11, b"\x00") + b"N" + b"\x00" * 4 + bytes([field_len]) + b"\x00" * 15
    body = header + field + b"\x0d"
    for dn in records:
        body += b" " + str(dn).encode("ascii").rjust(field_len)
    return body


# The real projection SPC's outlook shapefiles ship in (confirmed live
# 2026-09 by reading a real archive zip's own .prj) -- Lambert Conformal
# Conic, standard parallels 33N/45N, centered on 0/0. Every product's
# .prj in a given zip carries this identical definition.
_SPC_OUTLOOK_PRJ_WKT = (
    'PROJCS["Lambert_Conformal_Conic",GEOGCS["GCS_WGS_1984",'
    'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]],'
    'PROJECTION["Lambert_Conformal_Conic"],'
    'PARAMETER["standard_parallel_1",33],PARAMETER["standard_parallel_2",45],'
    'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
    'UNIT["Meter",1],PARAMETER["scale_factor",1.0]]'
)


def _build_outlook_zip(day, cycle_str, per_suffix, *, include_prj=True):
    """per_suffix: {suffix: [(ring, dn), ...]} -> zip bytes matching the
    real day{N}otlk_{cycle}_{suffix}.shp/.dbf(.prj) naming
    _parse_outlook_shapefile_zip looks for. Rings are given in the same
    projected (LCC easting/northing) coordinates a real .shp carries --
    not lon/lat -- since include_prj=True (the default, matching every
    real archive zip) means they get reprojected same as production."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for suffix, rows in per_suffix.items():
            rings = [ring for ring, _ in rows]
            dns = [dn for _, dn in rows]
            base = f"day{day}otlk_{cycle_str}_{suffix}"
            zf.writestr(f"{base}.shp", _build_shp(rings))
            zf.writestr(f"{base}.dbf", _build_dbf(dns))
            if include_prj:
                zf.writestr(f"{base}.prj", _SPC_OUTLOOK_PRJ_WKT)
    return buf.getvalue()


_TRIANGLE = [(-100.0, 35.0), (-99.0, 35.0), (-99.5, 36.0), (-100.0, 35.0)]


def test_shapefile_zip_url_matches_the_real_spc_archive_naming():
    from datetime import datetime, timezone
    cycle = datetime(2019, 6, 8, 13, 0, tzinfo=timezone.utc)
    assert _archive_spc_shapefile_zip_url(1, cycle) == (
        "https://www.spc.noaa.gov/products/outlook/archive/2019/"
        "day1otlk_20190608_1300-shp.zip"
    )


def test_categorical_dn_maps_to_the_expected_risk_label():
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {
        "cat": [(_TRIANGLE, 2), (_TRIANGLE, 4), (_TRIANGLE, 6)],
    })
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    labels = [f["properties"]["LABEL"] for f in by_suffix["cat"]]
    assert labels == ["MRGL", "ENH", "HIGH"]


def test_unmapped_categorical_dn_is_dropped_not_mislabeled():
    # DN=0/1 aren't real risk categories in SPC's cat.shp convention --
    # dropping them (rather than guessing a label) mirrors how the live
    # geojson path never emits an unlabeled categorical feature either.
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {
        "cat": [(_TRIANGLE, 0), (_TRIANGLE, 3)],
    })
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    assert len(by_suffix["cat"]) == 1
    assert by_suffix["cat"][0]["properties"]["LABEL"] == "SLGT"


def test_probabilistic_dn_passes_through_for_spc_prob_label_to_read():
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {
        "wind": [(_TRIANGLE, 15), (_TRIANGLE, 5)],
    })
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    assert [f["properties"]["DN"] for f in by_suffix["wind"]] == [15, 5]


def test_sig_layer_dn_zero_placeholder_is_dropped_but_real_sig_polygon_kept():
    # Regression test: confirmed live on 2019-06-08 that SPC ships a
    # degenerate DN=0 placeholder polygon in sigwind.shp/sigtorn.shp on a
    # day with no significant wind/tornado risk (the shapefile format
    # needs at least one record even when there's nothing to draw), while
    # sighail.shp that same day carried a real DN=10 significant-hail
    # contour. Rendering the DN=0 one would draw a false "significant"
    # overlay where none exists.
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {
        "sigwind": [(_TRIANGLE, 0)],
        "sigtorn": [(_TRIANGLE, 0)],
        "sighail": [(_TRIANGLE, 10)],
    })
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    assert by_suffix["sigwind"] == []
    assert by_suffix["sigtorn"] == []
    assert len(by_suffix["sighail"]) == 1
    assert by_suffix["sighail"][0]["properties"]["DN"] == 10


def test_missing_member_pair_is_absent_not_a_crash():
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {"cat": [(_TRIANGLE, 2)]})
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    assert "wind" not in by_suffix
    assert "cat" in by_suffix


def test_bad_zip_bytes_return_empty_not_a_crash():
    assert _parse_outlook_shapefile_zip(b"not a zip file") == {}


def _normalize(kind, features, **kwargs):
    raw = json.dumps({"type": "FeatureCollection", "features": features})
    return json.loads(_normalize_archive_spc_geojson(kind, raw, **kwargs))


def test_shapefile_categorical_features_get_the_cat_property_the_frontend_colors_by():
    # Regression test: _fetch_outlook's shapefile-fallback branch used to
    # serialize _parse_outlook_shapefile_zip's raw features directly,
    # which carry DN/LABEL but never props["cat"] -- the property
    # spc-cat-fill's paint expression (ui/map/map_template.html) actually
    # matches on. Every shapefile-sourced categorical feature silently
    # fell through to that match expression's default color regardless of
    # its real risk level, confirmed live on a real case (2010-05-10,
    # where a DN=6/HIGH area rendered as plain MRGL green).
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {
        "cat": [(_TRIANGLE, 2), (_TRIANGLE, 4), (_TRIANGLE, 6)],
    })
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    fc = _normalize("categorical", by_suffix["cat"], day=1, product="categorical")
    assert [f["properties"]["cat"] for f in fc["features"]] == ["MRGL", "ENH", "HIGH"]


def test_shapefile_probabilistic_features_get_a_percent_string_label():
    # Same bug as above for wind/hail/tornado: the frontend's probability
    # color scales (windHailColor/torColor) match on props["LABEL"] as a
    # percent string ("5", "15", "30", ...) -- the raw shapefile record
    # only carries DN (a bare int), never LABEL, unless routed through
    # _normalize_archive_spc_geojson (which derives it via
    # _spc_prob_label, same as the live geojson path already does).
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {
        "wind": [(_TRIANGLE, 15), (_TRIANGLE, 5)],
    })
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    fc = _normalize("wind", by_suffix["wind"], day=1, product="wind")
    assert [f["properties"]["LABEL"] for f in fc["features"]] == ["15", "5"]


def test_shapefile_significant_features_are_force_labeled_sign_after_normalizing():
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {
        "sighail": [(_TRIANGLE, 10)],
    })
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    fc = _normalize("significant", by_suffix["sighail"], day=1, product="hail", force_label="SIGN")
    assert fc["features"][0]["properties"]["LABEL"] == "SIGN"


def test_shapefile_geometry_is_reprojected_from_lcc_to_lon_lat():
    # Regression test for the actual "can't see the outlook" bug: .shp
    # coordinates are Lambert Conformal Conic easting/northing (meters),
    # not GeoJSON's required WGS84 lon/lat -- used directly (this
    # fallback's original behavior), a real Oklahoma categorical polygon
    # rendered nowhere near the visible map, confirmed live on
    # 2010-05-10 (its raw coordinates, misread as plain values, landed
    # in northern Canada instead of OK/TX/AR).
    from pyproj import CRS, Transformer
    crs = CRS.from_wkt(_SPC_OUTLOOK_PRJ_WKT)
    to_lcc = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    # Oklahoma City, projected into the same LCC easting/northing a real
    # .shp would store -- this is what the parser actually reads.
    okc_lon, okc_lat = -97.5, 35.5
    okc_x, okc_y = to_lcc.transform(okc_lon, okc_lat)
    ring_lcc = [
        (okc_x - 1000, okc_y - 1000), (okc_x + 1000, okc_y - 1000),
        (okc_x, okc_y + 1000), (okc_x - 1000, okc_y - 1000),
    ]

    zip_bytes = _build_outlook_zip(1, "20190608_2000", {"cat": [(ring_lcc, 2)]})
    by_suffix = _parse_outlook_shapefile_zip(zip_bytes)
    ring_out = by_suffix["cat"][0]["geometry"]["coordinates"][0]

    for lon, lat in ring_out:
        assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert abs(lon - okc_lon) < 0.1  # ~1 km ring around OKC, generous tolerance
        assert abs(lat - okc_lat) < 0.1


def test_missing_prj_refuses_to_guess_a_coordinate_system(caplog):
    # A .shp with no accompanying .prj is projected-CRS metadata this
    # parser has no basis to assume -- better to surface nothing (and
    # log why) than silently emit geometry at the wrong location.
    zip_bytes = _build_outlook_zip(1, "20190608_2000", {"cat": [(_TRIANGLE, 2)]}, include_prj=False)
    assert _parse_outlook_shapefile_zip(zip_bytes) == {}
    assert "no .prj found" in caplog.text
