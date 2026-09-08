"""Tests for PERiLS CopterSonde UAS ascent archive discovery/parsing."""

from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr

from archive.fetchers import coptersonde_archive_fetcher as csf


def _write_coptersonde_netcdf(path, base_time, time_offset, tdry_k, td_c, pres_pa,
                               wspd, wdir, alt_m, lat, lon):
    """Build a minimal file matching the real CopterSonde ascent schema."""
    data_vars = {
        "base_time": ((), np.int64(base_time)),
        "time_offset": ("time", np.array(time_offset, dtype="float64")),
        "tdry": ("time", np.array(tdry_k, dtype="float64")),
        "Td": ("time", np.array(td_c, dtype="float64")),
        "pres": ("time", np.array(pres_pa, dtype="float64")),
        "wspd": ("time", np.array(wspd, dtype="float64")),
        "dir": ("time", np.array(wdir, dtype="float64")),
        "alt": ("time", np.array(alt_m, dtype="float64")),
        "lat": ("time", np.array(lat, dtype="float64")),
        "lon": ("time", np.array(lon, dtype="float64")),
    }
    xr.Dataset(data_vars).to_netcdf(path, engine="h5netcdf")


def test_filename_re_extracts_site_date_time():
    m = csf._FILENAME_RE.match("PineyCreek5N940UACMTascent.c1.20220322.113559.cdf")
    assert m is not None
    assert m.group("site") == "PineyCreek5N940UACMTascent"
    assert m.group("date") == "20220322"
    assert m.group("time") == "113559"


def test_filename_re_rejects_non_matching_names():
    assert csf._FILENAME_RE.match("readme.txt") is None
    assert csf._FILENAME_RE.match("PineyCreek.a0.20220322.113559.cdf") is None


def test_parse_coptersonde_netcdf_converts_units(tmp_path):
    base_time = int(datetime(2022, 3, 22, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "site.c1.20220322.100017.cdf"
    _write_coptersonde_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 10.0],
        tdry_k=[290.0, 291.0],       # 16.85C, 17.85C
        td_c=[12.0, 12.5],
        pres_pa=[100000.0, 99900.0],  # 1000.0, 999.0 hPa
        wspd=[5.0, 5.0],
        wdir=[180.0, 180.0],          # due south -> u=0, v=+5 (northward)
        alt_m=[50.0, 60.0],
        lat=[35.0, 35.0001],
        lon=[-90.0, -90.0001],
    )

    snd = csf.parse_coptersonde_netcdf(path.read_bytes(), "site")

    assert snd is not None
    assert snd.temperature == pytest.approx([16.85, 17.85], abs=0.01)
    assert snd.pressure == pytest.approx([1000.0, 999.0], abs=0.01)
    assert list(snd.dewpoint) == [12.0, 12.5]
    assert list(snd.height) == [50.0, 60.0]
    # wind from due south (180 deg): u (eastward) ~0, v (northward) positive
    assert snd.u_wind[0] == pytest.approx(0.0, abs=1e-6)
    assert snd.v_wind[0] == pytest.approx(5.0, abs=1e-6)
    assert snd.lat == 35.0
    assert snd.valid_time == datetime(2022, 3, 22, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_coptersonde_netcdf_filters_fill_sentinel(tmp_path):
    # Confirmed on a real file: trailing samples carry the raw netCDF
    # float64 default fill (~9.97e36), not NaN, across alt/pres/lat/lon.
    fill = 9.969209968386869e36
    base_time = int(datetime(2022, 3, 22, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "site.c1.20220322.100017.cdf"
    _write_coptersonde_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0, 10.0, 20.0],
        tdry_k=[290.0, 290.0, 290.0],
        td_c=[12.0, 12.0, 12.0],
        pres_pa=[100000.0, fill, 99900.0],
        wspd=[5.0, 5.0, 5.0],
        wdir=[180.0, 180.0, 180.0],
        alt_m=[50.0, 60.0, fill],
        lat=[35.0, 35.0, 35.0],
        lon=[-90.0, -90.0, -90.0],
    )

    snd = csf.parse_coptersonde_netcdf(path.read_bytes(), "site")

    assert snd is not None
    assert len(snd.pressure) == 1  # only the first sample survives both fills
    assert snd.pressure[0] == pytest.approx(1000.0, abs=0.01)


def test_parse_coptersonde_netcdf_returns_none_when_all_invalid(tmp_path):
    fill = 9.969209968386869e36
    base_time = int(datetime(2022, 3, 22, tzinfo=timezone.utc).timestamp())
    path = tmp_path / "site.c1.20220322.100017.cdf"
    _write_coptersonde_netcdf(
        path,
        base_time=base_time,
        time_offset=[0.0],
        tdry_k=[290.0],
        td_c=[12.0],
        pres_pa=[fill],
        wspd=[5.0],
        wdir=[180.0],
        alt_m=[50.0],
        lat=[35.0],
        lon=[-90.0],
    )

    assert csf.parse_coptersonde_netcdf(path.read_bytes(), "site") is None


def test_fetch_coptersonde_soundings_groups_by_site_and_filters_date(monkeypatch):
    monkeypatch.setattr(csf, "KNOWN_PERILS_IOPS", (("2022", "IOP1"),))

    def fake_list(year, iop):
        return [
            "SiteA.c1.20220322.100000.cdf",
            "SiteA.c1.20220322.110000.cdf",
            "SiteB.c1.20220322.100500.cdf",
            "SiteA.c1.20220323.100000.cdf",  # different date, must be excluded
        ]

    monkeypatch.setattr(csf, "_list_catalog_filenames", fake_list)

    base_time = int(datetime(2022, 3, 22, tzinfo=timezone.utc).timestamp())

    def fake_fetch(request, timeout):
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                import io as _io
                p = _io.BytesIO()
                _write_coptersonde_netcdf(
                    p, base_time=base_time, time_offset=[0.0], tdry_k=[290.0],
                    td_c=[12.0], pres_pa=[100000.0], wspd=[5.0], wdir=[180.0],
                    alt_m=[50.0], lat=[35.0], lon=[-90.0],
                )
                return p.getvalue()
        return R()

    monkeypatch.setattr(csf, "_urlopen_with_retry", fake_fetch)

    results = csf.fetch_coptersonde_soundings(datetime(2022, 3, 22, tzinfo=timezone.utc))

    assert set(results.keys()) == {"SiteA", "SiteB"}
    assert len(results["SiteA"].soundings) == 2
    assert len(results["SiteB"].soundings) == 1
    assert results["SiteA"].source == "coptersonde"
    assert results["SiteA"].station_id == "SiteA"
