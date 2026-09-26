"""Tests for CLAMPS trailer surface-meteorology archive parsing."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr

from archive.fetchers.clamps_surface_archive_fetcher import (
    KNOWN_CLAMPS_SURFACE_SOURCES,
    _dewpoint_c_from_rh,
    parse_clamps_surface_netcdf,
)


def _write_clamps_mwr_netcdf(path, base_time, time_offset, sfc_temp, sfc_rh, sfc_pres,
                              sfc_wspd, sfc_wdir, lat=29.4, lon=-95.0):
    """Build a minimal file matching the real FRDD/CLAMPS MWR ingested schema
    (scalar per-file lat/lon -- a stationary trailer for that file)."""
    data_vars = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "sfc_temp": ("time", np.array(sfc_temp, dtype="float32")),
        "sfc_rh": ("time", np.array(sfc_rh, dtype="float32")),
        "sfc_pres": ("time", np.array(sfc_pres, dtype="float32")),
        "sfc_wspd": ("time", np.array(sfc_wspd, dtype="float32")),
        "sfc_wdir": ("time", np.array(sfc_wdir, dtype="float32")),
        "lat": ((), np.float32(lat)),
        "lon": ((), np.float32(lon)),
    }
    xr.Dataset(data_vars).to_netcdf(path, engine="h5netcdf")


def _write_clamps_met_tower_netcdf(path, base_time, time_offset, sfc_temp, sfc_rh, sfc_pres,
                                    sfc_wspd, sfc_wdir, lat, lon):
    """Build a minimal file matching the real met tower schema: per-record
    lat/lon arrays, confirmed varying within a single file on a real
    sample rather than a fixed per-file scalar."""
    data_vars = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "sfc_temp": ("time", np.array(sfc_temp, dtype="float32")),
        "sfc_rh": ("time", np.array(sfc_rh, dtype="float32")),
        "sfc_pres": ("time", np.array(sfc_pres, dtype="float32")),
        "sfc_wspd": ("time", np.array(sfc_wspd, dtype="float32")),
        "sfc_wdir": ("time", np.array(sfc_wdir, dtype="float32")),
        "lat": ("time", np.array(lat, dtype="float32")),
        "lon": ("time", np.array(lon, dtype="float32")),
    }
    xr.Dataset(data_vars).to_netcdf(path, engine="h5netcdf")


def test_dewpoint_c_from_rh_matches_known_reference_values():
    # 25C / 50% RH -> ~13.9C dewpoint (standard Magnus-Tetens reference case)
    assert _dewpoint_c_from_rh(25.0, 50.0) == pytest.approx(13.86, abs=0.05)
    # saturated air: dewpoint equals temperature
    assert _dewpoint_c_from_rh(20.0, 100.0) == pytest.approx(20.0, abs=0.05)


def test_dewpoint_c_from_rh_rejects_invalid_rh():
    assert _dewpoint_c_from_rh(20.0, 0.0) is None
    assert _dewpoint_c_from_rh(20.0, 101.0) is None


def test_parse_clamps_surface_netcdf_maps_fields(tmp_path):
    base_time = int(datetime(2022, 5, 25, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampsmwrC2.a1.20220525.000000.cdf"
    _write_clamps_mwr_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 3600.0],
        sfc_temp=[25.0, 20.0],
        sfc_rh=[50.0, 100.0],
        sfc_pres=[1005.0, 1004.0],
        sfc_wspd=[3.0, 5.0],
        sfc_wdir=[190.0, 200.0],
    )

    observations = parse_clamps_surface_netcdf(path.read_bytes(), "CLAMPS2")

    assert len(observations) == 2
    o = observations[0]
    assert o.timestamp == datetime(2022, 5, 25, 0, 0, 0, tzinfo=timezone.utc)
    assert o.vehicle_id == "CLAMPS2"
    assert o.temperature_c == 25.0
    assert o.dewpoint_c == pytest.approx(13.86, abs=0.05)
    assert o.pressure_mb == 1005.0
    assert o.wind_speed_ms == 3.0
    assert o.wind_dir_deg == 190.0
    assert o.lat == pytest.approx(29.4, abs=1e-4)
    # both records share the trailer's fixed site location
    assert observations[1].lat == o.lat and observations[1].lon == o.lon


def test_parse_clamps_surface_netcdf_drops_rows_missing_temperature(tmp_path):
    base_time = int(datetime(2022, 5, 25, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampsmwrC2.a1.20220525.000000.cdf"
    _write_clamps_mwr_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 600.0],
        sfc_temp=[25.0, np.nan],
        sfc_rh=[50.0, 50.0],
        sfc_pres=[1005.0, 1005.0],
        sfc_wspd=[3.0, 3.0],
        sfc_wdir=[190.0, 190.0],
    )

    observations = parse_clamps_surface_netcdf(path.read_bytes(), "CLAMPS2")

    assert len(observations) == 1


def test_known_clamps_surface_sources_cover_both_clamps_trailers_only():
    platform_dirs = {s.platform_dir for s in KNOWN_CLAMPS_SURFACE_SOURCES}
    assert platform_dirs == {"clamps/clamps1", "clamps/clamps2"}


def test_known_clamps_surface_sources_prefer_met_tower_over_mwr_for_clamps2():
    clamps2_sources = [s for s in KNOWN_CLAMPS_SURFACE_SOURCES if s.platform_dir == "clamps/clamps2"]
    assert [s.kind for s in clamps2_sources] == ["met_tower", "mwr"]


def test_parse_clamps_surface_netcdf_filters_fill_sentinel(tmp_path):
    # Confirmed on a real met tower file: missing values are a repeated
    # -999.0 with no declared _FillValue attribute, not NaN.
    base_time = int(datetime(2016, 5, 26, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampsmetC2.a1.20160526.000003.cdf"
    _write_clamps_met_tower_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 600.0],
        sfc_temp=[20.0, -999.0],
        sfc_rh=[-999.0, 60.0],
        sfc_pres=[960.0, -999.0],
        sfc_wspd=[-999.0, 4.0],
        sfc_wdir=[-999.0, 180.0],
        lat=[36.5, 36.6],
        lon=[-97.4, -97.3],
    )

    observations = parse_clamps_surface_netcdf(path.read_bytes(), "CLAMPS2")

    # second record has sfc_temp == -999.0 -> dropped entirely (mirrors
    # the "missing temperature" gate used for the MWR source)
    assert len(observations) == 1
    o = observations[0]
    assert o.temperature_c == 20.0
    assert o.dewpoint_c is None       # sfc_rh was the fill sentinel
    assert o.wind_speed_ms is None    # sfc_wspd was the fill sentinel
    assert o.pressure_mb == 960.0


def test_parse_clamps_surface_netcdf_uses_per_record_location(tmp_path):
    base_time = int(datetime(2016, 5, 26, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampsmetC2.a1.20160526.000003.cdf"
    _write_clamps_met_tower_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 600.0],
        sfc_temp=[20.0, 21.0],
        sfc_rh=[60.0, 60.0],
        sfc_pres=[960.0, 960.0],
        sfc_wspd=[3.0, 3.0],
        sfc_wdir=[180.0, 190.0],
        lat=[36.5, 36.6],
        lon=[-97.4, -97.3],
    )

    observations = parse_clamps_surface_netcdf(path.read_bytes(), "CLAMPS2")

    assert [o.lat for o in observations] == pytest.approx([36.5, 36.6], abs=1e-4)
    assert [o.lon for o in observations] == pytest.approx([-97.4, -97.3], abs=1e-4)


def test_parse_clamps_surface_netcdf_suppresses_wind_direction_when_not_trusted(tmp_path):
    # The met tower's own file attributes say heading correction "has not
    # been applied" to sfc_wdir -- must not be trusted without it.
    base_time = int(datetime(2016, 5, 26, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampsmetC2.a1.20160526.000003.cdf"
    _write_clamps_met_tower_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0],
        sfc_temp=[20.0],
        sfc_rh=[60.0],
        sfc_pres=[960.0],
        sfc_wspd=[3.0],
        sfc_wdir=[180.0],
        lat=[36.5],
        lon=[-97.4],
    )

    trusted = parse_clamps_surface_netcdf(path.read_bytes(), "CLAMPS2", trust_wind_direction=True)
    untrusted = parse_clamps_surface_netcdf(path.read_bytes(), "CLAMPS2", trust_wind_direction=False)

    assert trusted[0].wind_dir_deg == 180.0
    assert untrusted[0].wind_dir_deg is None
    # wind speed doesn't depend on heading and stays populated either way
    assert untrusted[0].wind_speed_ms == 3.0


def test_classic_netcdf_met_tower_is_read_without_netcdf4_dependency(tmp_path):
    path = tmp_path / "classic.cdf"
    # A real NETCDF3_CLASSIC file, not just a .cdf extension on an HDF5 file.
    dataset = xr.Dataset({
        "base_time": ((), np.int32(datetime(2024, 4, 27, tzinfo=timezone.utc).timestamp())),
        "time_offset": ("time", [0., 60.]),
        "sfc_temp": ("time", [25., 26.]), "sfc_rh": ("time", [50., 60.]),
        "sfc_pres": ("time", [1000., 1001.]), "sfc_wspd": ("time", [3., 4.]),
        "sfc_wdir": ("time", [180., 190.]),
        "lat": ("time", [35., 35.1]), "lon": ("time", [-97., -97.1]),
    })
    dataset.to_netcdf(path, engine="scipy", format="NETCDF3_CLASSIC")
    data = path.read_bytes()
    assert data.startswith(b"CDF")
    observations = parse_clamps_surface_netcdf(data, "CLAMPS1")
    assert len(observations) == 2
    assert observations[1].temperature_c == 26.
    assert observations[1].lat == 35.1
    assert observations[1].timestamp == datetime(2024, 4, 27, 0, 1, tzinfo=timezone.utc)


def test_files_are_found_from_the_catalog_whatever_their_start_time(monkeypatch):
    """Most CLAMPS1 MWR days have no .000000 file; guessing that name
    missed them. A day with a restart has two files; both are used."""
    from archive.fetchers import clamps_surface_archive_fetcher as module
    source = module.KNOWN_CLAMPS_SURFACE_SOURCES[1]          # CLAMPS1 MWR
    listing = [f"{source.datastream}.20190309.000512.cdf", f"{source.datastream}.20190309.141003.cdf",
               f"{source.datastream}.20190310.001031.cdf"]
    calls = []
    monkeypatch.setattr(module, "_LISTING_CACHE", {})
    monkeypatch.setattr(module, "_list_catalog_filenames", lambda *a: calls.append(a) or listing)
    urls = module._find_files(source, "20190309")
    assert [u.rsplit("/", 1)[1] for u in urls] == listing[:2]
    assert module._find_files(source, "20240427") == []
    assert len(calls) == 1                                    # the listing is fetched once


def test_a_failed_listing_is_not_cached(monkeypatch):
    from archive.fetchers import clamps_surface_archive_fetcher as module
    source = module.KNOWN_CLAMPS_SURFACE_SOURCES[1]
    answers = [[], [f"{source.datastream}.20190309.000512.cdf"]]
    monkeypatch.setattr(module, "_LISTING_CACHE", {})
    monkeypatch.setattr(module, "_list_catalog_filenames", lambda *a: answers.pop(0))
    assert module._find_files(source, "20190309") == []
    assert len(module._find_files(source, "20190309")) == 1


def test_overlapping_restart_files_are_merged_without_duplicates(monkeypatch):
    from io import BytesIO
    from archive.fetchers import clamps_surface_archive_fetcher as module
    from core.observation import Observation
    t = datetime(2019, 3, 9, 14, 10, tzinfo=timezone.utc)
    source = module.KNOWN_CLAMPS_SURFACE_SOURCES[1]
    monkeypatch.setattr(module, "KNOWN_CLAMPS_SURFACE_SOURCES", (source,))
    monkeypatch.setattr(module, "_find_files", lambda s, d: ["https://x/a", "https://x/b"])
    monkeypatch.setattr(module, "_urlopen_with_retry", lambda req, timeout: BytesIO(req.full_url.encode()))
    rows = {b"https://x/a": [t, t + timedelta(minutes=1)], b"https://x/b": [t + timedelta(minutes=1), t + timedelta(minutes=2)]}
    monkeypatch.setattr(module, "parse_clamps_surface_netcdf",
                        lambda data, key, trusted: [Observation(key, 35, -97, ts) for ts in rows[data]])
    obs = module.fetch_clamps_surface_observations(t)
    assert [o.timestamp for o in obs] == [t, t + timedelta(minutes=1), t + timedelta(minutes=2)]


def test_kelvin_temperatures_labelled_celsius_are_converted(tmp_path):
    """CLAMPS1 MWR files in 2023 store sfc_temp in kelvin under a degC label
    (e.g. 2023-06-09: median 305.3); STORM showed ~300 degC surface temps."""
    path = tmp_path / "mwr_kelvin.cdf"
    _write_clamps_mwr_netcdf(path, 1686268800, [0, 60, 120], [303.15, 304.15, -9999.0],
                             [50, 50, 50], [970, 970, 970], [3, 3, 3], [180, 180, 180])
    obs = parse_clamps_surface_netcdf(path.read_bytes(), "CLAMPS1")
    assert [round(o.temperature_c, 2) for o in obs] == [30.0, 31.0]      # fill value still dropped
    assert obs[0].dewpoint_c == pytest.approx(_dewpoint_c_from_rh(30.0, 50.0))


def test_celsius_files_are_left_alone(tmp_path):
    path = tmp_path / "mwr_c.cdf"
    _write_clamps_mwr_netcdf(path, 1686268800, [0, 60], [28.0, 29.0], [50, 50], [970, 970], [3, 3], [180, 180])
    assert [o.temperature_c for o in parse_clamps_surface_netcdf(path.read_bytes(), "CLAMPS1")] == [28.0, 29.0]
