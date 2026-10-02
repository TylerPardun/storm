"""Base states from the RAP/RUC analyses, computed the way MESO-VIEW's
build_base_state_fromRAP_RUC.py did (its tsc5 products), so perturbations
(theta_v', theta_e', ...) can be shown for any case.

For one analysis time:
  1. the RAP analysis of the nearest hour (RUC before 2012-05; 13 km grid
     130), pressure levels 1000-825 hPa: temperature, relative humidity,
     u, v and geopotential height;
  2. the grid points within +/-50 km (east-west and north-south) of the storm
     center -- every point of the grid-index rectangle spanning them;
  3. each column interpolated to height Z (m above sea level): T, mixing
     ratio, u, v linearly in height, pressure log-linearly, RH recovered
     from (p, T, w); outside the levels, the nearest two are extrapolated;
  4. physically impossible values dropped, then anything beyond 2 sigma in
     T, RH, p, u or v;
  5. qv, dewpoint, theta, theta_v, theta_e, theta_w computed at each point,
     and every quantity averaged over the points.

Thermodynamics use the same MetPy functions as the observations
(core/derived.py), so a perturbation compares like with like. That differs
from MESO-VIEW in two places: its pickles were made with MetPy <= 1.6
(Bolton's saturation vapor pressure; MetPy 1.7 uses Ambaum 2020), which
moves theta_e by up to ~0.2 K and theta_v by ~0.01 K; and its theta_w came
from a 1-hPa moist-adiabat descent (meso_view_theta_w=True reproduces it)
rather than MetPy's wet_bulb_potential_temperature (0.1-0.2 K apart).
scripts/validate_base_states.py measures both.

MESO-VIEW's tsc5 run clustered the points with DBSCAN (eps 30 km) and then
weighted the clusters; on a 13 km grid that is always one cluster, so the
result is the plain mean, which is what this computes. It applied no
inflow mask. Z was the mean USGS 3DEP elevation under the mobile mesonet's
footprint in the analysis window (core.terrain).

Winds: MESO-VIEW used the GRIB's grid-relative u/v as they were. `u`, `v`
here are rotated to true north; `u_grid`, `v_grid` keep MESO-VIEW's values.
Pressure is the model pressure at Z -- never reduced to another datum.
"""
from __future__ import annotations

import logging
import math
import os
import tempfile
from dataclasses import dataclass, asdict
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

LEVELS_HPA = (1000.0, 975.0, 950.0, 925.0, 900.0, 875.0, 850.0, 825.0)
HALF_WIDTH_KM = 50.0
SIGMA_K = 2.0
RAP_START = datetime(2012, 5, 1, tzinfo=timezone.utc)      # RUC before, RAP from
_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = _ROOT / "data" / "rap_cache"

_NCEI = "https://www.ncei.noaa.gov"


def model_hour(t: datetime) -> datetime:
    """The analysis used for time t: the nearest hour (minute >= 30 rounds up)."""
    t = t.astimezone(timezone.utc)
    return t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=t.minute // 30)


def analysis_urls(hour: datetime) -> list[str]:
    """Where NCEI keeps the analysis for `hour`, most likely first.
    (NCEI moved RAP from data/rapid-refresh to oa/prod-model in 2026; its
    THREDDS RAP collection only holds 2020-05 onward.)"""
    d = hour.astimezone(timezone.utc)
    ym, ymd, stamp = f"{d:%Y%m}", f"{d:%Y%m%d}", f"{d:%Y%m%d_%H%M}"
    if d < RAP_START:
        name = f"ruc2anl_130_{stamp}_000.grb2"
        return [f"{_NCEI}/thredds/fileServer/model-ruc130anl/{ym}/{ymd}/{name}"]
    name = f"rap_130_{stamp}_000.grb2"
    prod = f"{_NCEI}/oa/prod-model/rapid-refresh/access"
    urls = [f"{prod}/historical/analysis/{ym}/{ymd}/{name}",
            f"{prod}/rap-130-13km/analysis/{ym}/{ymd}/{name}",
            f"{_NCEI}/thredds/fileServer/model-rap130anl/{ym}/{ymd}/{name}"]
    if d.year >= 2021:
        urls = urls[1:] + urls[:1]
    return urls


def fetch_analysis(hour: datetime, cache_dir: Path = CACHE_DIR) -> Path | None:
    """The analysis GRIB for `hour`, downloaded once and kept in cache_dir;
    through core.package_sources, so case packages carry and serve it.
    None if NCEI has no analysis for the hour (404 everywhere);
    ConnectionError if it couldn't be reached (try again later)."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
    from core import package_sources
    cache_dir = Path(cache_dir)
    unreachable = None
    for url in analysis_urls(hour):
        target = cache_dir / url.rsplit("/", 1)[1]
        if target.is_file() and target.stat().st_size > 0:
            from core import provenance
            provenance.record("base state", url, sha256=None, size=target.stat().st_size, source="cache")
            return target
        try:
            data = package_sources.read_url("base state", Request(url, headers={"User-Agent": "STORM/1.0"}),
                                            urlopen, timeout=180)
        except HTTPError as exc:
            if exc.code == 404:
                continue
            log.warning("RAP/RUC %s: %s", url, exc)
            unreachable = exc
            continue
        except (URLError, OSError) as exc:
            log.warning("RAP/RUC %s: %s", url, exc)
            unreachable = exc
            continue
        cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
        return target
    if unreachable is not None:
        raise ConnectionError(f"RAP/RUC analysis for {hour:%Y-%m-%d %H}Z unreachable: {unreachable}")
    return None


@dataclass
class Columns:
    """The model columns around one center (nz, ny, nx), pressure levels first."""
    levels: np.ndarray        # hPa
    gh: np.ndarray            # m
    t: np.ndarray             # degC
    rh: np.ndarray            # %
    u: np.ndarray             # m/s, grid-relative
    v: np.ndarray
    lat: np.ndarray           # (ny, nx)
    lon: np.ndarray
    rotation: np.ndarray      # (ny, nx) radians, grid -> earth wind rotation


def _subset_index(clat, clon, lat2d, lon2d, half_km):
    lon2d = ((lon2d + 180) % 360) - 180
    clon = ((clon + 180) % 360) - 180
    deg2km = 111.32
    dx = (((lon2d - clon + 180) % 360) - 180) * math.cos(math.radians(clat)) * deg2km
    dy = (lat2d - clat) * deg2km
    inside = (np.abs(dx) <= half_km) & (np.abs(dy) <= half_km)
    if not inside.any():
        raise ValueError("no grid points within the box")
    i, j = np.where(inside)
    return slice(i.min(), i.max() + 1), slice(j.min(), j.max() + 1)


@lru_cache(maxsize=2)        # the 5-minute analysis times of one hour share a file
def _fields(path: str) -> dict:
    import xarray as xr
    out = {}
    for short in ("t", "r", "u", "v", "gh"):
        ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={
            "indexpath": "", "filter_by_keys": {"typeOfLevel": "isobaricInhPa", "shortName": short}})
        ds = ds.sel(isobaricInhPa=list(LEVELS_HPA))
        out[short] = np.asarray(ds[short].values, dtype=float)
        if short == "u":
            out["u_attrs"] = dict(ds["u"].attrs)
            out["lat"] = np.asarray(ds["latitude"].values, dtype=float)
            out["lon"] = np.asarray(ds["longitude"].values, dtype=float)
    return out


def read_columns(path: Path, clat: float, clon: float, half_km: float = HALF_WIDTH_KM) -> Columns:
    """T, RH, u, v, geopotential height on LEVELS_HPA within +/-half_km of
    the center, from a RAP/RUC grid-130 GRIB2 file."""
    f = _fields(str(path))
    ys, xs = _subset_index(clat, clon, f["lat"], f["lon"], half_km)
    lat, lon = f["lat"][ys, xs], ((f["lon"][ys, xs] + 180) % 360) - 180
    cut = lambda short: f[short][:, ys, xs]
    return Columns(levels=np.array(LEVELS_HPA), gh=cut("gh"), t=cut("t") - 273.15, rh=cut("r"),
                   u=cut("u"), v=cut("v"), lat=lat, lon=lon, rotation=_wind_rotation(f["u_attrs"], lon))


def _wind_rotation(attrs: dict, lon) -> np.ndarray:
    """Angle to turn grid-relative winds to earth-relative on a Lambert
    conformal grid (zero if the GRIB says they're earth-relative already)."""
    if int(attrs.get("GRIB_uvRelativeToGrid", 1)) == 0:
        return np.zeros_like(lon, dtype=float)
    lov = float(attrs.get("GRIB_LoVInDegrees", 265.0))
    lat1 = math.radians(float(attrs.get("GRIB_Latin1InDegrees", 25.0)))
    lat2 = math.radians(float(attrs.get("GRIB_Latin2InDegrees", 25.0)))
    if abs(lat1 - lat2) < 1e-9:
        n = math.sin(lat1)
    else:
        n = (math.log(math.cos(lat1) / math.cos(lat2))
             / math.log(math.tan(math.pi / 4 + lat2 / 2) / math.tan(math.pi / 4 + lat1 / 2)))
    lov = ((lov + 180) % 360) - 180
    return np.radians(n * (((lon - lov + 180) % 360) - 180))


def _interp_to_height(cols: Columns, z_m: float):
    """(p, T, w, u, v, u_grid, v_grid) at height z_m for every column, as
    MESO-VIEW's state_at_height/pressure_at_height."""
    from metpy.calc import mixing_ratio_from_relative_humidity
    from metpy.units import units
    z3, T3, RH3, U3, V3, p1 = cols.gh, cols.t, cols.rh, cols.u, cols.v, cols.levels.astype(float)
    if np.nanmean(np.diff(z3[:, 0, 0])) < 0:
        z3, T3, RH3, U3, V3, p1 = z3[::-1], T3[::-1], RH3[::-1], U3[::-1], V3[::-1], p1[::-1]
    nz, ny, nx = z3.shape
    p3 = p1[:, None, None] * np.ones_like(z3)
    w3 = mixing_ratio_from_relative_humidity(p3 * units.hPa, T3 * units.degC, RH3 * units.percent).m
    Z = np.full((ny, nx), float(z_m))
    cross = (z3[:-1] <= Z) & (z3[1:] >= Z)
    k = np.where(cross.any(axis=0), cross.argmax(axis=0),
                 np.where(Z < np.nanmin(z3, axis=0), 0,
                          np.where(Z > np.nanmax(z3, axis=0), nz - 2, cross.argmax(axis=0))))
    jj, ii = np.meshgrid(np.arange(ny), np.arange(nx), indexing="ij")
    zk, zk1 = z3[k, jj, ii], z3[k + 1, jj, ii]
    frac = np.where(np.abs(zk1 - zk) > 0, (Z - zk) / (zk1 - zk), 0.0)
    lerp = lambda f: f[k, jj, ii] + frac * (f[k + 1, jj, ii] - f[k, jj, ii])
    T, w, ug, vg = lerp(T3), lerp(w3), lerp(U3), lerp(V3)
    p = np.exp((1 - frac) * np.log(p1[k]) + frac * np.log(p1[k + 1]))
    cos, sin = np.cos(cols.rotation), np.sin(cols.rotation)
    u, v = cos * ug + sin * vg, -sin * ug + cos * vg
    return p, T, w, u, v, ug, vg


def _theta_w(T, p, rh) -> float:
    """MESO-VIEW's compute_thw: LCL, then moist adiabat down to 1000 hPa (K)."""
    import metpy.calc as mc
    from metpy.units import units
    try:
        td = mc.dewpoint_from_relative_humidity(T * units.degC, rh * units.percent).m
        lcl_p, lcl_t = mc.lcl(p * units.hPa, T * units.degC, td * units.degC)
        levels = np.arange(int(lcl_p.m), 1001, 1)
        prof = mc.moist_lapse(levels * units.hPa, lcl_t, reference_pressure=lcl_p)
        return float(prof[-1].m) + 273.15
    except (ValueError, IndexError):
        return float("nan")


def _within_k_sigma(a, base, k=SIGMA_K):
    sub = a[base & np.isfinite(a)]
    if sub.size < 8:
        return np.ones(a.shape, dtype=bool)
    mu, sig = np.nanmean(sub), np.nanstd(sub, ddof=1)
    if not np.isfinite(sig) or sig <= 0:
        return np.ones(a.shape, dtype=bool)
    return np.isfinite(a) & (np.abs(a - mu) <= k * sig)


@dataclass
class BaseState:
    time: str                 # analysis time, ISO UTC
    model: str                # "RAP" or "RUC"
    model_hour: str
    center_lat: float
    center_lon: float
    z_m: float                # height the state is at (m MSL)
    n_points: int
    pres: float               # hPa at z_m (model pressure, not reduced)
    temp: float               # degC
    dew: float
    rh: float                 # %
    qv: float                 # kg/kg (mixing ratio)
    th: float                 # K
    thv: float
    the: float
    thw: float
    u: float                  # m/s, earth-relative
    v: float
    u_grid: float             # grid-relative (MESO-VIEW's u, v)
    v_grid: float

    def to_dict(self) -> dict:
        return asdict(self)


def compute(cols: Columns, z_m: float, *, time: datetime, hour: datetime,
            center: tuple[float, float], meso_view_theta_w: bool = False) -> BaseState | None:
    """The base state at height z_m from the columns around the center."""
    import metpy.calc as mc
    from metpy.units import units
    p, T, w, u, v, ug, vg = _interp_to_height(cols, z_m)
    rh = mc.relative_humidity_from_mixing_ratio(p * units.hPa, T * units.degC,
                                                w * units.dimensionless).to("percent").m
    flat = lambda a: np.asarray(a, dtype=float).ravel()
    p, T, rh, ug, vg, u, v = map(flat, (p, T, rh, ug, vg, u, v))
    lon = cols.lon.ravel()
    ok = (np.isfinite(cols.lat.ravel()) & np.isfinite(lon) & (lon < 0)
          & np.isfinite(T) & np.isfinite(rh) & np.isfinite(p) & np.isfinite(ug) & np.isfinite(vg)
          & (T > -100) & (T < 120) & (rh >= 0) & (rh <= 110) & (p > 100) & (p < 1050)
          & (ug > -100) & (ug < 120) & (vg > -100) & (vg < 120))
    keep = ok & _within_k_sigma(T, ok) & _within_k_sigma(rh, ok) & _within_k_sigma(p, ok) \
        & _within_k_sigma(ug, ok) & _within_k_sigma(vg, ok)
    if not keep.any():
        return None
    p, T, rh, ug, vg, u, v = (a[keep] for a in (p, T, rh, ug, vg, u, v))
    pq, Tq, rhq = p * units.hPa, T * units.degC, rh * units.percent
    qv = mc.mixing_ratio_from_relative_humidity(pq, Tq, rhq).m
    dew = mc.dewpoint_from_relative_humidity(Tq, rhq).m
    th = mc.potential_temperature(pq, Tq).m
    thv = mc.virtual_potential_temperature(pq, Tq, qv * units.dimensionless).m
    the = mc.equivalent_potential_temperature(pq, Tq, dew * units.degC).m
    if meso_view_theta_w:
        thw = np.array([_theta_w(T[i], p[i], rh[i]) for i in range(p.size)])
    else:
        thw = mc.wet_bulb_potential_temperature(pq, Tq, dew * units.degC).to("K").m
    mean = lambda a: float(np.nanmean(a)) if np.isfinite(a).any() else float("nan")
    iso = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return BaseState(time=iso(time), model="RUC" if hour < RAP_START else "RAP", model_hour=iso(hour),
                     center_lat=float(center[0]), center_lon=float(center[1]), z_m=float(z_m),
                     n_points=int(p.size), pres=mean(p), temp=mean(T), dew=mean(dew), rh=mean(rh),
                     qv=mean(qv), th=mean(th), thv=mean(thv), the=mean(the), thw=mean(thw),
                     u=mean(u), v=mean(v), u_grid=mean(ug), v_grid=mean(vg))


def base_state_at(time: datetime, center: tuple[float, float], z_m: float,
                  cache_dir: Path = CACHE_DIR, meso_view_theta_w: bool = False) -> BaseState | None:
    """Fetch (or reuse) the analysis and compute the base state."""
    hour = model_hour(time)
    path = fetch_analysis(hour, cache_dir)
    if path is None:
        log.warning("Base state %s: no RAP/RUC analysis found for %s", time, hour)
        return None
    cols = read_columns(path, center[0], center[1])
    return compute(cols, z_m, time=time, hour=hour, center=center, meso_view_theta_w=meso_view_theta_w)


# ---- a case's base states, one per 5-minute analysis time ---------------------
ANALYSIS_STEP_S = 300          # analysis times on the clock's 5-minute marks, each covering +/-2.5 min


def analysis_epoch(t: float) -> int:
    """The analysis time (epoch s) whose window contains epoch time t."""
    return int((t + ANALYSIS_STEP_S / 2) // ANALYSIS_STEP_S * ANALYSIS_STEP_S)


def analysis_epochs(start: float, end: float) -> list[int]:
    return list(range(analysis_epoch(start), analysis_epoch(end) + 1, ANALYSIS_STEP_S))


@dataclass(frozen=True)
class Inputs:
    """What one analysis time's base state depends on."""
    epoch: int
    center: tuple[float, float]     # storm center (lat, lon)
    center_source: str              # "track" or "observations"
    z_m: float                      # mean ground elevation under the mobile mesonet
    n_obs: int                      # mobile observations in the window

    @property
    def key(self) -> tuple:
        return (self.epoch, round(self.center[0], 3), round(self.center[1], 3), round(self.z_m, 1))


def inputs_for(epoch: int, mobile_columns: list[dict], track_points=(), *, elevation=None) -> Inputs | None:
    """The center and Z for one analysis time, MESO-VIEW's way: center from
    the storm track (else the mobile observations' mean position); Z the
    mean ground elevation over the lat/lon box of the mobile mesonet's
    positions in the window. None when no mobile observation is in it;
    ConnectionError when the terrain can't be read (try again later).
    mobile_columns: core.derived.observation_arrays() of each mobile platform."""
    from core import derived, terrain
    lo, hi = epoch - ANALYSIS_STEP_S / 2, epoch + ANALYSIS_STEP_S / 2
    lats, lons = [], []
    for cols in mobile_columns:
        t = cols["time"]
        sel = (t >= lo) & (t < hi) & np.isfinite(cols["lat"]) & np.isfinite(cols["lon"])
        lats.append(cols["lat"][sel])
        lons.append(cols["lon"][sel])
    lat, lon = (np.concatenate(lats), np.concatenate(lons)) if lats else (np.array([]), np.array([]))
    if lat.size == 0:
        return None
    z = (elevation or terrain.footprint_elevation)(lat, lon)
    if not math.isfinite(z):
        raise ConnectionError("ground elevation unavailable (terrain tiles could not be read)")
    clat, clon = derived.track_center(list(track_points), np.array([float(epoch)]))
    if np.isfinite(clat[0]):
        center, source = (float(clat[0]), float(clon[0])), "track"
    else:
        center, source = (float(lat.mean()), float(lon.mean())), "observations"
    return Inputs(epoch, center, source, float(z), int(lat.size))


class BaseStateSeries:
    """Base states by analysis time; looked up for any observation time."""

    def __init__(self):
        self._states: dict[int, BaseState] = {}
        self._inputs: dict[int, Inputs] = {}

    def __len__(self) -> int:
        return len(self._states)

    def has(self, inputs: Inputs) -> bool:
        have = self._inputs.get(inputs.epoch)
        return have is not None and have.key == inputs.key

    def put(self, inputs: Inputs, state: BaseState | None) -> None:
        self._inputs[inputs.epoch] = inputs
        if state is None:
            self._states.pop(inputs.epoch, None)
        else:
            self._states[inputs.epoch] = state

    def get(self, epoch: int) -> BaseState | None:
        return self._states.get(epoch)

    def values(self, field: str, times: np.ndarray) -> np.ndarray:
        """The base state's `field` for each observation time (NaN where the
        observation's analysis window has no base state)."""
        out = np.full(np.shape(times), np.nan)
        for i, t in enumerate(np.asarray(times, dtype=float)):
            state = self._states.get(analysis_epoch(t))
            if state is not None:
                out[i] = getattr(state, field)
        return out

    def to_json(self) -> list[dict]:
        return [{**asdict(self._inputs[e]), "state": self._states[e].to_dict()} for e in sorted(self._states)]
