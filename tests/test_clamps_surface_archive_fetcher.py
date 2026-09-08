"""Tests for CLAMPS trailer surface-meteorology archive parsing."""

from datetime import datetime, timezone

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
    """Build a minimal file matching the real FRDD/CLAMPS MWR ingested schema."""
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
