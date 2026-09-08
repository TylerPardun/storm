"""Tests for CLAMPS TROPoe thermodynamic-profile archive parsing."""

from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr

from archive.fetchers.clamps_tropoe_archive_fetcher import parse_clamps_tropoe_netcdf


def _write_tropoe_netcdf(path, base_time, time_offset, height_km, temperature, dewpt,
                          pressure, qc_flag, converged_flag, lat=29.4, lon=-95.0, alt=20.0):
    """Build a minimal file matching the real FRDD/CLAMPS TROPoe schema."""
    data_vars = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "height": ("height", np.array(height_km, dtype="float64")),
        "temperature": (("time", "height"), np.array(temperature, dtype="float32")),
        "dewpt": (("time", "height"), np.array(dewpt, dtype="float32")),
        "pressure": (("time", "height"), np.array(pressure, dtype="float32")),
        "qc_flag": ("time", np.array(qc_flag, dtype="float32")),
        "converged_flag": ("time", np.array(converged_flag, dtype="float32")),
        "lat": ((), np.float32(lat)),
        "lon": ((), np.float32(lon)),
        "alt": ((), np.float32(alt)),
    }
    xr.Dataset(data_vars).to_netcdf(path, engine="h5netcdf")


def test_parse_clamps_tropoe_netcdf_maps_fields_and_height_to_msl(tmp_path):
    base_time = int(datetime(2022, 5, 25, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampstropoe10.aeri.v1.C2.20220525.001005.nc"
    _write_tropoe_netcdf(
        path,
        base_time=base_time,
        time_offset=[1200.0],
        height_km=[0.0, 0.1, 1.0],
        temperature=[[25.0, 24.0, 18.0]],
        dewpt=[[20.0, 19.0, 12.0]],
        pressure=[[1005.0, 995.0, 900.0]],
        qc_flag=[0],
        converged_flag=[1],
        alt=20.0,
    )

    soundings = parse_clamps_tropoe_netcdf(path.read_bytes(), "CLAMPS2")

    assert len(soundings) == 1
    s = soundings[0]
    assert s.valid_time == datetime(2022, 5, 25, 0, 20, 0, tzinfo=timezone.utc)
    assert list(s.height) == [20.0, 120.0, 1020.0]  # km AGL -> m, + 20m site alt
    assert list(s.temperature) == [25.0, 24.0, 18.0]
    assert list(s.dewpoint) == [20.0, 19.0, 12.0]
    assert list(s.pressure) == [1005.0, 995.0, 900.0]
    assert np.isnan(s.u_wind).all() and np.isnan(s.v_wind).all()
    assert s.lat == pytest.approx(29.4, abs=1e-4) and s.lon == pytest.approx(-95.0, abs=1e-4)


def test_parse_clamps_tropoe_netcdf_drops_unconverged_and_qc_failed_rows(tmp_path):
    # Mirrors a real observed file: a rejected retrieval still has
    # plausible-looking values (a flat unconverged profile), not NaN --
    # only qc_flag/converged_flag reliably say whether to trust it.
    base_time = int(datetime(2022, 5, 25, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampstropoe10.aeri.v1.C2.20220525.001005.nc"
    _write_tropoe_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 600.0, 1200.0],
        height_km=[0.0, 1.0],
        temperature=[[20.0, 15.0], [25.0, 25.0], [21.0, 16.0]],
        dewpt=[[15.0, 10.0], [25.0, 25.0], [16.0, 11.0]],
        pressure=[[1000.0, 900.0], [1000.0, 900.0], [1000.0, 900.0]],
        qc_flag=[0, 2, 0],           # middle row: QC-rejected
        converged_flag=[1, 1, 2],    # third row: did not converge
    )

    soundings = parse_clamps_tropoe_netcdf(path.read_bytes(), "CLAMPS2")

    assert len(soundings) == 1
    assert list(soundings[0].temperature) == [20.0, 15.0]


def test_parse_clamps_tropoe_netcdf_assigns_sequential_slot_offsets(tmp_path):
    base_time = int(datetime(2022, 5, 25, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "clampstropoe10.aeri.v1.C2.20220525.001005.nc"
    _write_tropoe_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 600.0, 1200.0],
        height_km=[0.0],
        temperature=[[20.0], [21.0], [22.0]],
        dewpt=[[15.0], [16.0], [17.0]],
        pressure=[[1000.0], [999.0], [998.0]],
        qc_flag=[0, 0, 0],
        converged_flag=[1, 1, 1],
    )

    soundings = parse_clamps_tropoe_netcdf(path.read_bytes(), "CLAMPS2")

    assert [s.slot_offset for s in soundings] == [0, 1, 2]
    assert [s.temperature[0] for s in soundings] == [20.0, 21.0, 22.0]
