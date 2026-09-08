"""Tests for CLAMPS Doppler-lidar wind-profile archive parsing."""

from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr

from archive.fetchers.clamps_wind_archive_fetcher import (
    KNOWN_CLAMPS_WIND_SOURCES,
    parse_clamps_wind_netcdf,
)
from core.vad import MS_TO_KNOT


def _write_clamps_wind_netcdf(path, base_time, time_offset, height_km, wspd, wdir, rms=None, r_sq=None):
    """Build a minimal file matching the real FRDD/CLAMPS dlvad/dlcsmwinds
    schema, including the undeclared sentinel-fill quirk found in a real
    file (see parse_clamps_wind_netcdf's docstring)."""
    n_time = len(time_offset)
    n_height = len(height_km)
    data_vars = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "height": ("height", np.array(height_km, dtype="float64")),
        "wspd": (("time", "height"), np.array(wspd, dtype="float32")),
        "wdir": (("time", "height"), np.array(wdir, dtype="float32")),
    }
    if rms is not None:
        data_vars["rms"] = (("time", "height"), np.array(rms, dtype="float32"))
    if r_sq is not None:
        data_vars["r_sq"] = (("time", "height"), np.array(r_sq, dtype="float32"))
    xr.Dataset(data_vars).to_netcdf(path, engine="h5netcdf")


def test_parse_clamps_wind_netcdf_converts_units_and_time(tmp_path):
    base_time = int(datetime(2022, 5, 24, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampsdlvadC1.c1.20220524.000000.cdf"
    _write_clamps_wind_netcdf(
        path,
        base_time=base_time,
        time_offset=[3600.0],
        height_km=[0.1, 0.2],
        wspd=[[10.0, 12.0]],   # m/s
        wdir=[[270.0, 280.0]],
        rms=[[1.0, 1.5]],      # m/s
        r_sq=[[0.9, 0.8]],
    )

    profiles = parse_clamps_wind_netcdf(path.read_bytes(), "CLAMPS1-VAD")

    assert len(profiles) == 1
    p = profiles[0]
    assert p.timestamp == datetime(2022, 5, 24, 1, 0, 0, tzinfo=timezone.utc)
    assert p.site == "CLAMPS1-VAD"
    assert list(p.heights_m) == [100.0, 200.0]  # km -> m
    assert p.wind_spd[0] == approx(10.0 * MS_TO_KNOT)
    assert p.rms_error == approx(1.25 * MS_TO_KNOT)  # mean of [1.0, 1.5]


def test_parse_clamps_wind_netcdf_drops_cells_flagged_bad_by_r_sq(tmp_path):
    # Mirrors a real observed file: a bad retrieval is not NaN in wspd/rms
    # (wspd used a repeated finite sentinel, rms used -999.0), but r_sq
    # reliably flags the same cells with a large negative value.
    base_time = int(datetime(2022, 5, 25, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampsdlvadC1.c1.20220525.000000.cdf"
    _write_clamps_wind_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 600.0],
        height_km=[0.1, 0.2, 0.3],
        wspd=[
            [5.0, 1412.7993, 6.0],   # middle cell is the bad sentinel
            [1412.7993, 1412.7993, 1412.7993],  # entire row bad
        ],
        wdir=[[100.0, 999.0, 110.0], [999.0, 999.0, 999.0]],
        rms=[[0.5, -999.0, 0.6], [-999.0, -999.0, -999.0]],
        r_sq=[[0.9, -999.0, 0.8], [-999.0, -999.0, -999.0]],
    )

    profiles = parse_clamps_wind_netcdf(path.read_bytes(), "CLAMPS1-VAD")

    # second timestep has no valid cells at all and must be dropped entirely
    assert len(profiles) == 1
    p = profiles[0]
    assert list(p.heights_m) == [100.0, 300.0]  # middle (bad) height dropped
    assert p.wind_spd[0] == approx(5.0 * MS_TO_KNOT)
    assert p.wind_spd[1] == approx(6.0 * MS_TO_KNOT)
    assert p.rms_error == approx(((0.5 + 0.6) / 2) * MS_TO_KNOT)


def test_known_clamps_wind_sources_cover_dltruck_and_both_clamps_trailers():
    platform_dirs = {s.platform_dir for s in KNOWN_CLAMPS_WIND_SOURCES}
    assert "dltruck/dltruck1" in platform_dirs
    assert "clamps/clamps1" in platform_dirs
    assert "clamps/clamps2" in platform_dirs


def approx(value, rel=1e-4):
    return pytest.approx(value, rel=rel)
