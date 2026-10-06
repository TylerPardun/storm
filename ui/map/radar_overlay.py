
import io
import logging
import math
from time import perf_counter
import numpy as np

from PyQt6.QtCore import QObject
import matplotlib
matplotlib.use("Agg")   # non-interactive backend — no display needed
import matplotlib.cm as mcm
import matplotlib.colors as mcolors
import matplotlib.image as mimg
from scipy.ndimage import map_coordinates

from core.radar_scan import RadarScan
from core.mem_probe import peak_rss_mb, log_delta

log = logging.getLogger(__name__)

# --- memory safety gate (temporary diagnostic instrumentation) -------------
# On an 8 GB machine, ARCHIVE_SUPERRES_GRID_SIZE (4096px) renders are the
# prime suspect for the reported ~8 GB session that forced a restart: a
# 4096x4096 grid is ~28x the cells of the normal 768px playback grid, and
# each render allocates several float64 arrays that size. Tier-1/live/NOXP
# renders (<=1536px) are never gated -- only the super-res tier is, and only
# once peak RSS actually gets close to the ceiling. Once tripped, stays
# tripped for the rest of the process (no cooldown/reset logic to get wrong).
_SUPERRES_SAFETY_RSS_CEILING_MB = 6144.0   # ~75% of this machine's 8 GB
_SUPERRES_GATE_MIN_GRID = 2048             # only gates render calls at/above this size
_superres_gate_tripped = False

# output grid resolution for polar→Cartesian reprojection.
RENDER_GRID_SIZE = 768
MAX_RENDER_GRID_SIZE = 1536
ADAPTIVE_RENDER_GRID = True
ADAPTIVE_GRID_STEPS = (128, 192, 256, 320, 384, 512, 640, 768, 1024, 1280, 1536)
ADAPTIVE_DOWN_MS = 280.0
ADAPTIVE_UP_MS = 130.0
ADAPTIVE_DOWN_SCANS = 2
ADAPTIVE_UP_SCANS = 4

# Archive-only "super-res" tier (see ui/app/main_window.py's debounced Tier-2
# render). Deliberately separate from RENDER_GRID_SIZE/MAX_RENDER_GRID_SIZE
# above, which live mode's adaptive algorithm owns — these constants must
# never feed back into that path.
ARCHIVE_SUPERRES_GRID_SIZE = 4096       # matches MESO-VIEW's default output_size_px
ARCHIVE_SUPERRES_CROP_RADIUS_M = 300_000.0  # 300 km half-width; ~146 m/px at 4096px


# function to set the render grid size (used on startup)
def set_render_grid_size(n: int) -> None:
    # pylint: disable=global-statement
    global RENDER_GRID_SIZE

    # clamp to a practical range; higher values are expensive for PNG rendering.
    RENDER_GRID_SIZE = max(64, min(MAX_RENDER_GRID_SIZE, n))

    # log it
    log.info("RENDER_GRID_SIZE set to %d", RENDER_GRID_SIZE)


def set_adaptive_render_grid(enabled: bool) -> None:
    """Enable or disable adaptive resolution scaling. Call before MainWindow is created."""
    # pylint: disable=global-statement
    global ADAPTIVE_RENDER_GRID
    ADAPTIVE_RENDER_GRID = enabled
    log.info("ADAPTIVE_RENDER_GRID set to %s", enabled)



def _rgba255(r: int, g: int, b: int, a: int = 255) -> tuple[float, float, float, float]:
    return (r / 255.0, g / 255.0, b / 255.0, a / 255.0)


def _make_nws_ref_cmap():
    """RadarScope-style base reflectivity palette for -32 to 90 dBZ."""
    vmin = -32.0
    vmax = 90.0
    stops = [
        (-32.0, _rgba255(0, 0, 0, 0)),
        (0.0,   _rgba255(139, 147, 146, 0)),
        (5.0,   _rgba255(92, 109, 137, 70)),
        (10.0,  _rgba255(57, 85, 134, 141)),
        (15.0,  _rgba255(76, 125, 156, 212)),
        (20.0,  _rgba255(60, 173, 110, 255)),
        (25.0,  _rgba255(21, 125, 30, 255)),
        (30.0,  _rgba255(155, 191, 3, 255)),
        (35.0,  _rgba255(230, 223, 0, 255)),
        (40.0,  _rgba255(250, 148, 0, 255)),
        (45.0,  _rgba255(212, 116, 6, 255)),
        (50.0,  _rgba255(249, 35, 11, 255)),
        (55.0,  _rgba255(186, 37, 22, 255)),
        (60.0,  _rgba255(202, 153, 180, 255)),
        (65.0,  _rgba255(197, 87, 145, 255)),
        (70.0,  _rgba255(154, 36, 224, 255)),
        (75.0,  _rgba255(105, 24, 179, 255)),
        (80.0,  _rgba255(132, 253, 255, 255)),
        (85.0,  _rgba255(98, 181, 196, 255)),
        (90.0,  _rgba255(161, 101, 73, 255)),
        (94.5,  _rgba255(115, 10, 1, 255)),
    ]
    colors = [
        (max(0.0, min(1.0, (value - vmin) / (vmax - vmin))), rgba)
        for value, rgba in stops
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_ref",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    cmap.set_over(_rgba255(115, 10, 1, 255))
    return cmap


def _make_nws_vel_cmap():
    """RadarScope-like base velocity palette with a neutral zero center."""
    stops = [
        (-75.0, (0.020, 0.078, 0.820, 1.00)),  # deep blue
        (-60.0, (0.090, 0.365, 0.980, 1.00)),  # royal blue
        (-45.0, (0.145, 0.765, 0.980, 1.00)),  # cyan
        (-32.0, (0.000, 0.930, 0.780, 1.00)),  # aqua
        (-22.0, (0.000, 0.820, 0.220, 1.00)),  # green
        (-12.0, (0.000, 0.540, 0.000, 1.00)),  # dark green
        (-6.0,  (0.220, 0.420, 0.220, 1.00)),  # muted green
        (-2.0,  (0.420, 0.480, 0.420, 1.00)),  # near-zero inbound tint
        (0.0,   (0.600, 0.600, 0.600, 1.00)),  # neutral gray
        (2.0,   (0.480, 0.380, 0.380, 1.00)),  # near-zero outbound tint
        (6.0,   (0.520, 0.140, 0.140, 1.00)),  # dark red
        (12.0,  (0.730, 0.000, 0.000, 1.00)),  # red
        (22.0,  (0.950, 0.000, 0.000, 1.00)),  # bright red
        (32.0,  (1.000, 0.310, 0.000, 1.00)),  # orange-red
        (45.0,  (1.000, 0.620, 0.000, 1.00)),  # orange
        (60.0,  (1.000, 0.950, 0.000, 1.00)),  # yellow
        (75.0,  (1.000, 0.980, 0.820, 1.00)),  # pale yellow
    ]

    vmin, vmax = -75.0, 75.0
    colors = [
        ((value - vmin) / (vmax - vmin), rgba)
        for value, rgba in stops
    ]

    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_vel",
        colors,
        N=1024
    )
    cmap.set_under(alpha=0)
    cmap.set_over(alpha=0)
    return cmap


def _make_nws_cc_cmap():
    """Correlation coefficient colormap (low=transparent, high=blue/white)."""
    colors = [
        (0.00, (1.000, 1.000, 1.000, 1.00)),  # white
        (0.45, (0.000, 0.000, 0.000, 1.00)),  # black
        (0.60, (0.039, 0.039, 0.745, 1.00)),  # blue
        (0.75, (0.471, 0.471, 1.000, 1.00)),  # light blue
        (0.80, (0.373, 0.961, 0.392, 1.00)),  # green
        (0.85, (0.529, 0.843, 0.039, 1.00)),  # yellow-green
        (0.90, (1.000, 1.000, 0.000, 1.00)),  # yellow
        (0.95, (1.000, 0.549, 0.000, 1.00)),  # orange
        (0.97, (0.882, 0.012, 0.000, 1.00)),  # red
        (0.99, (0.545, 0.118, 0.302, 1.00)),  # dark magenta
        (1.00, (1.000, 0.706, 0.843, 1.00)),  # pink
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_cc",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_zdr_cmap():
    """NWS-style differential reflectivity colormap (-4 to +8 dB)."""
    colors = [
        (0.00, (0.20, 0.20, 0.80, 1.00)),   # blue (negative ZDR)
        (0.25, (0.40, 0.70, 1.00, 1.00)),   # light blue
        (0.33, (0.90, 0.90, 0.90, 0.50)),   # gray (near 0)
        (0.45, (0.20, 0.80, 0.20, 1.00)),   # green
        (0.60, (1.00, 1.00, 0.00, 1.00)),   # yellow
        (0.75, (1.00, 0.50, 0.00, 1.00)),   # orange
        (1.00, (1.00, 0.00, 0.00, 1.00)),   # red (high positive ZDR)
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_zdr",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_sw_cmap():
    """Spectrum width colormap (0–30 kt)."""
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),   # transparent / 0
        (0.10, (0.20, 0.20, 0.60, 0.70)),   # dark blue
        (0.30, (0.20, 0.60, 1.00, 1.00)),   # blue
        (0.50, (0.00, 0.90, 0.90, 1.00)),   # cyan
        (0.70, (0.00, 0.80, 0.00, 1.00)),   # green
        (0.85, (1.00, 1.00, 0.00, 1.00)),   # yellow
        (1.00, (1.00, 0.00, 0.00, 1.00)),   # red
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_sw",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_phi_cmap():
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),
        (0.15, (0.10, 0.20, 0.70, 0.90)),
        (0.35, (0.20, 0.70, 1.00, 1.00)),
        (0.55, (0.00, 0.85, 0.40, 1.00)),
        (0.75, (1.00, 0.90, 0.00, 1.00)),
        (1.00, (1.00, 0.20, 0.00, 1.00)),
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_phi",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_kdp_cmap():
    """AWIPS-style KDP colormap for -2 to +10 deg/km."""
    colors = [
        (0.00, (0.502, 0.000, 0.502, 1.00)),  # -2.0: purple
        (0.17, (0.000, 0.000, 1.000, 1.00)),  # -0.5: blue
        (0.21, (0.000, 1.000, 1.000, 1.00)),  #  0.0: cyan
        (0.25, (0.000, 0.804, 0.000, 1.00)),  #  0.5: green
        (0.29, (0.000, 0.502, 0.000, 1.00)),  #  1.0: dark green
        (0.33, (1.000, 1.000, 0.000, 1.00)),  #  1.5: yellow
        (0.42, (1.000, 0.647, 0.000, 1.00)),  #  2.5: orange
        (0.50, (1.000, 0.000, 0.000, 1.00)),  #  3.5: red
        (0.58, (0.545, 0.000, 0.000, 1.00)),  #  4.5: dark red
        (0.67, (1.000, 0.753, 0.796, 1.00)),  #  5.5: pink
        (0.75, (0.502, 0.000, 0.502, 1.00)),  #  6.5: purple
        (0.83, (1.000, 1.000, 1.000, 1.00)),  #  7.5: white
        (1.00, (0.663, 0.663, 0.663, 1.00)),  # 10.0: gray
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_kdp",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


def _make_nws_cfp_cmap():
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),
        (0.15, (0.15, 0.15, 0.18, 0.35)),
        (0.35, (0.28, 0.28, 0.34, 0.60)),
        (0.55, (0.55, 0.55, 0.62, 0.80)),
        (0.75, (0.82, 0.82, 0.86, 0.95)),
        (1.00, (1.00, 1.00, 1.00, 1.00)),
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_cfp",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    return cmap


NWS_REF_CMAP = _make_nws_ref_cmap()
NWS_VEL_CMAP = _make_nws_vel_cmap()
NWS_CC_CMAP  = _make_nws_cc_cmap()
NWS_ZDR_CMAP = _make_nws_zdr_cmap()
NWS_SW_CMAP  = _make_nws_sw_cmap()
NWS_PHI_CMAP = _make_nws_phi_cmap()
NWS_KDP_CMAP = _make_nws_kdp_cmap()
NWS_CFP_CMAP = _make_nws_cfp_cmap()

COLORMAPS = {
    "nws_ref": NWS_REF_CMAP,
    "nws_vel": NWS_VEL_CMAP,
    "nws_cc":  NWS_CC_CMAP,
    "nws_zdr": NWS_ZDR_CMAP,
    "nws_sw":  NWS_SW_CMAP,
    "nws_phi": NWS_PHI_CMAP,
    "nws_kdp": NWS_KDP_CMAP,
    "nws_cfp": NWS_CFP_CMAP,
}


# two corrections live here:

_MERC_R = 6378137.0
_MERC_LAT_LIMIT = 85.05112878
_R_SPHERE_M = 6371000.0


def _lonlat_to_merc(lon, lat):
    lat_c = np.clip(lat, -_MERC_LAT_LIMIT, _MERC_LAT_LIMIT)
    x = np.deg2rad(lon) * _MERC_R
    y = np.log(np.tan(np.pi / 4 + np.deg2rad(lat_c) / 2)) * _MERC_R
    return x, y


def _merc_to_lonlat(x, y):
    lon = np.rad2deg(x / _MERC_R)
    lat = np.rad2deg(2 * np.arctan(np.exp(y / _MERC_R)) - np.pi / 2)
    return lon, lat


def _haversine_inverse_polar(
    radar_lat: float,
    radar_lon: float,
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Spherical inverse: from (radar_lat, radar_lon) to each grid cell,
    return (range_m, azimuth_deg) where azimuth is degrees clockwise from
    true north (NEXRAD convention)."""
    phi1 = math.radians(radar_lat)
    lam1 = math.radians(radar_lon)
    phi2 = np.deg2rad(lat_grid)
    lam2 = np.deg2rad(lon_grid)
    dphi = phi2 - phi1
    dlam = lam2 - lam1

    a = np.sin(dphi / 2.0) ** 2 + math.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2.0) ** 2
    range_m = 2.0 * _R_SPHERE_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))

    y = np.sin(dlam) * np.cos(phi2)
    x = math.cos(phi1) * np.sin(phi2) - math.sin(phi1) * np.cos(phi2) * np.cos(dlam)
    az_deg = (np.rad2deg(np.arctan2(y, x))) % 360.0
    return range_m, az_deg



def _scan_sampler(sample_scan: RadarScan):
    """A function (lat_grid, lon_grid) -> values, sampling the scan by nearest
    gate. The scan's gate geometry is worked out once here, so a big image
    can be sampled a strip at a time."""
    num_az, num_rng = sample_scan.data.shape

    radar_lat = float(sample_scan.lats[:, 0].mean())
    radar_lon = float(sample_scan.lons[:, 0].mean())

    # recover gate-center spacing from the scan's own polar grid (spherical
    gate_range_m, _ = _haversine_inverse_polar(
        radar_lat, radar_lon, sample_scan.lats, sample_scan.lons
    )
    gate_centers_m = np.median(gate_range_m, axis=0)
    first_gate_m   = float(gate_centers_m[0])
    if num_rng > 1:
        gate_width_m = float(np.median(np.diff(gate_centers_m)))
    else:
        gate_width_m = float(np.nanmax(gate_range_m) or 1.0)
    if not np.isfinite(gate_width_m) or gate_width_m <= 0:
        gate_width_m = float(np.nanmax(gate_range_m)) / max(num_rng, 1)
    del gate_range_m

    far_edge_m = first_gate_m + (num_rng - 0.5) * gate_width_m
    near_edge_m = max(0.0, first_gate_m - 0.5 * gate_width_m)
    sentinel = sample_scan.vmin - 999.0
    data_filled = np.where(np.isnan(sample_scan.data), sentinel, sample_scan.data)

    def sample(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
        range_m, az_deg = _haversine_inverse_polar(radar_lat, radar_lon, lat_grid, lon_grid)
        az_idx  = ((az_deg - sample_scan.az_offset) % 360.0) * num_az / 360.0
        rng_idx = (range_m - first_gate_m) / gate_width_m
        outside = (range_m > far_edge_m) | (range_m < near_edge_m)
        coords = np.array([az_idx.ravel(), rng_idx.ravel()])
        sampled = map_coordinates(
            data_filled, coords, order=0, prefilter=False, mode="constant", cval=sentinel
        ).reshape(lat_grid.shape)
        sampled[outside | (sampled <= sentinel + 1.0)] = np.nan
        return sampled
    return sample


def _sample_scan_to_grid(
    sample_scan: RadarScan,
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
) -> np.ndarray:
    """Sample a scan onto an arbitrary lat/lon grid using nearest-neighbor.

    Polar lookup is spherical (haversine distance + true bearing) so it
    matches the spherical forward formula in data/radar_decoder.py.  Gate
    width and first-gate offset are recovered from the scan's own lat/lon
    arrays so the index mapping is consistent with the gate-center
    convention used by the decoder.
    """
    return _scan_sampler(sample_scan)(lat_grid, lon_grid)


# Rows per strip when rendering: the image is built a strip at a time so a
# 4096 px render needs tens of MB at once instead of most of a GB (whole-grid
# float64 coordinates, samples and RGBA). The result is identical.
RENDER_STRIP_ROWS = 256


def render_scan_to_png(
    scan: RadarScan,
    grid_size: int,
    mask_scan: RadarScan | None = None,
    crop_radius_m: float | None = None,
) -> tuple[bytes, list, float]:
    """
    Convert a RadarScan to a PNG.  Fully thread-safe — takes all inputs
    as parameters and creates its own ScalarMappable; never touches shared state.

    crop_radius_m: when given, the output covers a fixed square of this
    half-width (in Web Mercator meters) centered on the scan's own radar
    lat/lon, instead of the full scan extent. None (default) preserves the
    original full-extent behavior for every existing caller.

    Returns:
        (png_bytes, [west, south, east, north], elapsed_ms)
    """
    global _superres_gate_tripped
    IMG = grid_size
    t0 = perf_counter()
    rss_before = peak_rss_mb()

    if IMG >= _SUPERRES_GATE_MIN_GRID:
        if _superres_gate_tripped:
            raise RuntimeError(
                f"super-res render skipped: memory safety gate already tripped "
                f"this session (grid={IMG})"
            )
        if rss_before >= _SUPERRES_SAFETY_RSS_CEILING_MB:
            _superres_gate_tripped = True
            log.warning(
                "[memprobe] SAFETY GATE TRIPPED: peak RSS %.0f MB >= %.0f MB ceiling -- "
                "disabling the %dpx super-res tier for the rest of this session",
                rss_before, _SUPERRES_SAFETY_RSS_CEILING_MB, IMG,
            )
            raise RuntimeError(
                f"super-res render skipped: peak RSS {rss_before:.0f} MB >= "
                f"{_SUPERRES_SAFETY_RSS_CEILING_MB:.0f} MB safety ceiling"
            )

    # build the output PNG grid uniform in Web Mercator so MapLibre's image
    if crop_radius_m is not None:
        radar_lat = float(scan.lats[:, 0].mean())
        radar_lon = float(scan.lons[:, 0].mean())
        cx, cy = _lonlat_to_merc(radar_lon, radar_lat)
        cx = float(cx)
        cy = float(cy)
        x_min = cx - crop_radius_m
        x_max = cx + crop_radius_m
        y_min = cy - crop_radius_m
        y_max = cy + crop_radius_m
    else:
        all_x, all_y = _lonlat_to_merc(scan.lons, scan.lats)
        x_min = float(np.nanmin(all_x))
        x_max = float(np.nanmax(all_x))
        y_min = float(np.nanmin(all_y))
        y_max = float(np.nanmax(all_y))

    out_x = np.linspace(x_min, x_max, IMG)
    out_y = np.linspace(y_max, y_min, IMG)   # rows top→bottom

    cmap   = COLORMAPS.get(scan.colormap, NWS_REF_CMAP)
    norm   = mcolors.Normalize(vmin=scan.vmin, vmax=scan.vmax, clip=False)
    mapper = mcm.ScalarMappable(norm=norm, cmap=cmap)
    sample = _scan_sampler(scan)
    sample_mask = (_scan_sampler(mask_scan)
                   if scan.colormap in ("nws_vel", "nws_cc", "nws_kdp") and mask_scan is not None else None)

    rgba = np.empty((IMG, IMG, 4), dtype=np.uint8)
    for r0 in range(0, IMG, RENDER_STRIP_ROWS):
        r1 = min(IMG, r0 + RENDER_STRIP_ROWS)
        x_grid, y_grid = np.meshgrid(out_x, out_y[r0:r1])
        lon_grid, lat_grid = _merc_to_lonlat(x_grid, y_grid)
        del x_grid, y_grid
        data_out = sample(lat_grid, lon_grid)
        if scan.colormap == "nws_ref":
            data_out[~np.isnan(data_out) & (data_out < 8.0)] = np.nan
        elif sample_mask is not None:
            ref_out = sample_mask(lat_grid, lon_grid)
            data_out[np.isnan(ref_out) | (ref_out < 8.0)] = np.nan
        strip = mapper.to_rgba(data_out, bytes=True)
        strip[np.isnan(data_out), 3] = 0
        rgba[r0:r1] = strip

    buf = io.BytesIO()
    mimg.imsave(buf, rgba, format="png")
    png_bytes = buf.getvalue()

    elapsed_ms = (perf_counter() - t0) * 1000.0
    log.debug(
        "render_scan_to_png: %.0f KB PNG in %.1f ms (grid=%d)",
        len(png_bytes) / 1024,
        elapsed_ms,
        IMG,
    )
    log_delta(
        f"render_scan_to_png grid={IMG} site={getattr(scan, 'site', '?')} "
        f"t={getattr(scan, 'scan_time', '?')}",
        rss_before, peak_rss_mb(), elapsed_ms,
    )

    lon_w, lat_s = _merc_to_lonlat(np.array(x_min), np.array(y_min))
    lon_e, lat_n = _merc_to_lonlat(np.array(x_max), np.array(y_max))
    bounds = [float(lon_w), float(lat_s), float(lon_e), float(lat_n)]
    return png_bytes, bounds, elapsed_ms



class RadarOverlay(QObject):
    """
    Manages the radar image overlay on the MapLibre map.

    Works by:
    1. Converting RadarScan data → RGBA PNG via matplotlib
    2. Encoding PNG as base64
    3. Injecting into MapLibre as an image source with known lat/lon bounds
    4. Adding a raster layer that displays the image

    The overlay is updated in-place when new scans arrive.
    """

    LAYER_ID  = "radar-overlay"
    SOURCE_ID = "radar-image"

    def __init__(self, map_widget, parent=None, layer_id: str | None = None,
                 source_id: str | None = None, use_scheme_handler: bool = True):
        """layer_id/source_id let a second, independent overlay (e.g. a
        CLAMPS lidar layer) coexist with the primary radar one instead of
        fighting over the same MapLibre source. use_scheme_handler=False
        keeps this instance off the scheme handler's single shared PNG slot
        (storm://app/radar/overlay.png) -- that slot is radar's; a second
        overlay writing to it would have each overlay's hide()/inject()
        clobber the other's image, so a secondary overlay always injects
        via a plain base64 data URL instead."""
        super().__init__(parent)
        self._map = map_widget
        if layer_id is not None:
            self.LAYER_ID = layer_id
        if source_id is not None:
            self.SOURCE_ID = source_id
        self._use_scheme_handler = use_scheme_handler
        self._active = False
        self._hidden = False
        self._grid_size = int(RENDER_GRID_SIZE)
        self._adaptive_grid = ADAPTIVE_RENDER_GRID
        self._fast_render_streak = 0
        self._slow_render_streak = 0
        # cache ScalarMappable objects keyed by (colormap, vmin, vmax)
        _MAPPER_CACHE_MAX = 32
        self._mapper_cache_max = _MAPPER_CACHE_MAX
        self._mapper_cache: dict[tuple, mcm.ScalarMappable] = {}

    def update(self, scan: RadarScan, mask_scan: RadarScan | None = None):
        """render and display a new radar scan (synchronous, for loop playback)."""

        try:
            png_bytes, bounds = self._render_to_png(scan, mask_scan=mask_scan)
        except Exception as e:
            log.error("[RadarOverlay] render failed: %s", e, exc_info=True)
            return

        self.inject(png_bytes, bounds)
        log.info("[RadarOverlay] updated with %s (grid=%d)", scan.label, self._grid_size)

    def clear(self):
        """Visually clear the radar without destroying the MapLibre source (avoids render deadlocks)."""
        self.hide(transient=False)
        self._active = False
        self._hidden = False

    def hide(self, transient: bool = True):
        """
        Hide the overlay instantly by forcing 0 opacity and pushing a 1x1 transparent PNG.
        """
        self._hidden = True

        # always force opacity 0 and inject a 1x1 PNG to clear the GPU pipeline
        js = f"""
        (function() {{
            try {{
                if (typeof map !== 'undefined') {{
                    if (map.getLayer("{self.LAYER_ID}")) {{
                        map.setPaintProperty("{self.LAYER_ID}", "raster-opacity", 0);
                    }}
                    var src = map.getSource("{self.SOURCE_ID}");
                    if (src && src.updateImage) {{
                        var tinyPng = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";
                        var coords = (window._stormLastCoords && window._stormLastCoords["{self.SOURCE_ID}"]) || [[0,0],[0,0],[0,0],[0,0]];
                        src.updateImage({{url: tinyPng, coordinates: coords}});
                    }}
                }}
            }} catch(e) {{ console.error("STORM Hide error:", e); }}
        }})();
        """
        self._map.run_js(js)

        # clear the Python-side scheme handler
        try:
            scheme_handler = getattr(self._map, "scheme_handler", None) if self._use_scheme_handler else None
            if scheme_handler is not None:
                import base64
                tiny = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")
                scheme_handler.set_radar_png(tiny)
        except Exception:
            pass

        if transient:
            try:
                self._map.run_js(
                    "if (typeof window !== 'undefined') { "
                    "window._stormRadarSuspend = window._stormRadarSuspend || {}; "
                    f'window._stormRadarSuspend["{self.SOURCE_ID}"] = true; }}'
                )
            except Exception:
                pass

    def inject(self, png_bytes: bytes, bounds: list):
        """Inject a pre-rendered PNG into the map."""
        import time
        scheme_handler = getattr(self._map, "scheme_handler", None) if self._use_scheme_handler else None
        if scheme_handler is not None:
            scheme_handler.set_radar_png(png_bytes)
            ts = int(time.monotonic() * 1000) & 0xFFFFFF  # cache-bust token
            image_url = f"storm://app/radar/overlay.png?t={ts}"
        else:
            import base64
            image_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        
        restoring = self._hidden
        self._inject_into_map(image_url, bounds, restore_opacity=restoring)
        self._hidden = False
        self._active = True


    def _render_to_png(
        self,
        scan: RadarScan,
        mask_scan: RadarScan | None = None,
    ) -> tuple[bytes, list]:
        """
        Convert scan data to a PNG using proper polar→Cartesian reprojection.

        Returns:
            (png_bytes, [west, south, east, north])
        """
        IMG = self._grid_size   # adaptive configurable — lower = faster render
        t0 = perf_counter()

        log.debug(
            "rendering %s — grid=%dx%d, colormap=%s, vmin=%.1f vmax=%.1f",
            scan.label, IMG, IMG, scan.colormap, scan.vmin, scan.vmax
        )

        # build the output grid uniform in Web Mercator (see render_scan_to_png
        all_x, all_y = _lonlat_to_merc(scan.lons, scan.lats)
        x_min = float(np.nanmin(all_x))
        x_max = float(np.nanmax(all_x))
        y_min = float(np.nanmin(all_y))
        y_max = float(np.nanmax(all_y))

        out_x = np.linspace(x_min, x_max, IMG)
        out_y = np.linspace(y_max, y_min, IMG)   # rows top→bottom
        x_grid, y_grid = np.meshgrid(out_x, out_y)
        lon_grid, lat_grid = _merc_to_lonlat(x_grid, y_grid)

        data_out = _sample_scan_to_grid(scan, lat_grid, lon_grid)
        # for reflectivity only: mask sub-threshold pixels (~8 dBZ matches RadarScope)
        if scan.colormap == "nws_ref":
            data_out[~np.isnan(data_out) & (data_out < 8.0)] = np.nan
        elif scan.colormap in ("nws_vel", "nws_cc", "nws_kdp") and mask_scan is not None:
            ref_out = _sample_scan_to_grid(mask_scan, lat_grid, lon_grid)
            data_out[np.isnan(ref_out) | (ref_out < 8.0)] = np.nan

        # reuse cached ScalarMappable — recreating norm+cmap every frame is wasteful
        cache_key = (scan.colormap, scan.vmin, scan.vmax)
        if cache_key not in self._mapper_cache:
            if len(self._mapper_cache) >= self._mapper_cache_max:
                self._mapper_cache.pop(next(iter(self._mapper_cache)))
            cmap = COLORMAPS.get(scan.colormap, NWS_REF_CMAP)
            norm = mcolors.Normalize(vmin=scan.vmin, vmax=scan.vmax, clip=False)
            self._mapper_cache[cache_key] = mcm.ScalarMappable(norm=norm, cmap=cmap)
            log.debug("created new ScalarMappable for key %s", cache_key)
        mapper = self._mapper_cache[cache_key]

        rgba = mapper.to_rgba(data_out, bytes=True)   # (IMG, IMG, 4) uint8
        rgba[np.isnan(data_out), 3] = 0               # transparent for no-data pixels

        # encode as PNG
        buf = io.BytesIO()
        mimg.imsave(buf, rgba, format="png")
        png_bytes = buf.getvalue()

        elapsed_ms = (perf_counter() - t0) * 1000.0
        self._maybe_adjust_grid(elapsed_ms)
        log.debug(
            "render complete: %.0f KB PNG in %.1f ms (grid=%d)",
            len(png_bytes) / 1024,
            elapsed_ms,
            IMG,
        )

        # lat/lon corners corresponding to the mercator extent corners.
        lon_w, lat_s = _merc_to_lonlat(np.array(x_min), np.array(y_min))
        lon_e, lat_n = _merc_to_lonlat(np.array(x_max), np.array(y_max))
        bounds = [float(lon_w), float(lat_s), float(lon_e), float(lat_n)]
        return png_bytes, bounds

    def _maybe_adjust_grid(self, elapsed_ms: float) -> None:
        if not self._adaptive_grid:
            return

        steps = ADAPTIVE_GRID_STEPS
        if self._grid_size not in steps:
            self._grid_size = min(steps, key=lambda s: abs(s - self._grid_size))

        idx = steps.index(self._grid_size)
        changed = False
        prev = self._grid_size

        if elapsed_ms > ADAPTIVE_DOWN_MS:
            self._slow_render_streak += 1
            self._fast_render_streak = 0
            if self._slow_render_streak >= ADAPTIVE_DOWN_SCANS and idx > 0:
                self._grid_size = steps[idx - 1]
                self._slow_render_streak = 0
                changed = True
        elif elapsed_ms < ADAPTIVE_UP_MS:
            self._fast_render_streak += 1
            self._slow_render_streak = 0
            if self._fast_render_streak >= ADAPTIVE_UP_SCANS and idx < len(steps) - 1:
                self._grid_size = steps[idx + 1]
                self._fast_render_streak = 0
                changed = True
        else:
            self._fast_render_streak = 0
            self._slow_render_streak = 0

        if changed:
            log.info(
                "[RadarOverlay] adaptive grid %d -> %d (render=%.1f ms, down>%dms up<%dms)",
                prev,
                self._grid_size,
                elapsed_ms,
                int(ADAPTIVE_DOWN_MS),
                int(ADAPTIVE_UP_MS),
            )

    def _inject_into_map(self, image_url: str, bounds: list, restore_opacity: bool = False):
        """Add or update the radar image source and layer in MapLibre."""
        west, south, east, north = bounds
        coords_js = f"[[{west},{north}], [{east},{north}], [{east},{south}], [{west},{south}]]"

        if self._hidden and not restore_opacity:
            return

        js = f"""
        (function() {{
          try {{
              const imageUrl = "{image_url}";
              const coords   = {coords_js};
              window._stormLastCoords = window._stormLastCoords || {{}};
              window._stormLastCoords["{self.SOURCE_ID}"] = coords;
              window._stormRadarSuspend = window._stormRadarSuspend || {{}};

              if (window._stormRadarSuspend["{self.SOURCE_ID}"] && !{str(restore_opacity).lower()}) {{
                  return;
              }}
              window._stormRadarSuspend["{self.SOURCE_ID}"] = false;

              if (typeof map !== 'undefined') {{
                  if (map.getSource("{self.SOURCE_ID}")) {{
                      map.getSource("{self.SOURCE_ID}").updateImage({{
                          url: imageUrl,
                          coordinates: coords
                      }});
                      // always restore opacity if we are actively injecting an image
                      if (map.getLayer("{self.LAYER_ID}")) {{
                          map.setPaintProperty("{self.LAYER_ID}", "raster-opacity", 0.75);
                      }}
                  }} else {{
                      map.addSource("{self.SOURCE_ID}", {{
                          type: "image",
                          url: imageUrl,
                          coordinates: coords
                      }});
                      try {{
                          map.addLayer({{
                              id: "{self.LAYER_ID}",
                              type: "raster",
                              source: "{self.SOURCE_ID}",
                              paint: {{
                                  "raster-opacity": 0.75,
                                  "raster-fade-duration": 0
                              }}
                          }}, "road-unpaved");
                      }} catch(e) {{
                          // fallback if beforeId doesn't exist
                          map.addLayer({{
                              id: "{self.LAYER_ID}",
                              type: "raster",
                              source: "{self.SOURCE_ID}",
                              paint: {{
                                  "raster-opacity": 0.75,
                                  "raster-fade-duration": 0
                              }}
                          }});
                      }}
                  }}
              }}
              // a layer the user hid (e.g. the radar under a lidar scan) stays hidden
              if (map.getLayer("{self.LAYER_ID}") && window._stormLayerHidden && window._stormLayerHidden["{self.LAYER_ID}"]) {{
                  map.setLayoutProperty("{self.LAYER_ID}", "visibility", "none");
              }}
          }} catch(e) {{ console.error("STORM Inject error:", e); }}
        }})();
        """
        self._map.run_js(js)
