
import logging
import re
import requests
from io import BytesIO
from datetime import datetime, timezone, timedelta
from typing import Optional
from xml.etree import ElementTree

import numpy as np
from metpy.io import Level3File

from core.vad import VADProfile, VADSet

from archive import thredds_paths as paths
log = logging.getLogger(__name__)

# thredds base for Level 3 NVW products
THREDDS_CATALOG = f"{paths.catalog_root(paths.UCAR)}/{paths.UCAR_NEXRAD_LEVEL3}/NVW"
THREDDS_FILE = f"{paths.file_root(paths.UCAR)}/{paths.UCAR_NEXRAD_LEVEL3}/NVW"

# thredds catalog XML namespace
_NS = {"thredds": paths.THREDDS_NS}

# regex to extract HHMM from NVW filenames
_FNAME_RE = re.compile(r"Level3_\w+_NVW_\d{8}_(\d{4})\.nids")


def _normalize_site(site: str) -> str:
    """Convert 4-letter ICAO (KTLX) to 3-letter NEXRAD ID (TLX) for THREDDS."""
    if len(site) == 4 and site[0] in ("K", "P", "T"):
        return site[1:]
    return site


def _list_nvw_files(site: str, date_str: str) -> list[str]:
    """Query the THREDDS catalog XML for available NVW filenames on a given date.

    Returns a list of filenames sorted newest-first, e.g.
    ['Level3_TLX_NVW_20260412_0507.nids', ...].
    """
    url = f"{THREDDS_CATALOG}/{site}/{date_str}/catalog.xml"
    try:
        resp = requests.get(url, timeout=12)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.debug(f"Cannot list NVW catalog for {site}/{date_str}: {e}")
        return []

    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError as e:
        log.warning(f"Bad catalog XML for {site}/{date_str}: {e}")
        return []

    names = []
    for ds in root.iter(f"{{{_NS['thredds']}}}dataset"):
        name = ds.get("name", "")
        if name.endswith(".nids") and "NVW" in name:
            names.append(name)

    # sort newest-first by filename (embedded HHMM)
    names.sort(reverse=True)
    return names


def _parse_nvw_product(f: Level3File, site: str, timestamp: datetime) -> Optional[VADProfile]:
    """Parse a MetPy Level3File NVW product into a VADProfile.

    The NVW product is a graphical chart.  Wind barbs are stored as symbology
    packets with keys (x, y, direc, speed, color).  The y-axis labels give
    altitude in thousands of feet; we build a y->altitude lookup via np.interp
    and map each barb to its altitude.  Only the most-recent time column
    (rightmost) is extracted.
    """
    if not hasattr(f, "sym_block") or not f.sym_block:
        return None

    packets = f.sym_block[0]

    # separate wind-barb packets from text-label packets
    barb_pkts = []
    text_pkts = []
    for pkt in packets:
        if not isinstance(pkt, dict):
            continue
        if "direc" in pkt and "speed" in pkt:
            barb_pkts.append(pkt)
        elif "text" in pkt:
            text_pkts.append(pkt)

    if not barb_pkts:
        log.debug(f"No wind-barb packets in NVW for {site}")
        return None

    alt_labels: list[tuple[float, float]] = []  # (y, kft)
    for pkt in text_pkts:
        if pkt["x"] >= 10 or pkt["y"] >= 120:
            continue
        txt = pkt["text"].strip()
        try:
            kft = float(txt)
            alt_labels.append((pkt["y"], kft))
        except ValueError:
            continue

    if len(alt_labels) < 2:
        log.warning(f"Insufficient altitude labels in NVW for {site}")
        return None

    alt_labels.sort(key=lambda a: a[0])  # ascending y
    y_coords = np.array([a[0] for a in alt_labels])
    kft_vals = np.array([a[1] for a in alt_labels])

    unique_x = sorted({pkt["x"][0] for pkt in barb_pkts}, reverse=True)
    latest_x = unique_x[0]

    heights = []
    directions = []
    speeds = []
    for pkt in barb_pkts:
        if pkt["x"][0] != latest_x:
            continue
        y = pkt["y"][0]
        # map y to altitude via interpolation of the axis labels.
        alt_kft = float(np.interp(y, y_coords, kft_vals))
        alt_m = alt_kft * 1000.0 * 0.3048  # KFT → metres

        heights.append(alt_m)
        directions.append(pkt["direc"][0])
        speeds.append(pkt["speed"][0])

    if not heights:
        log.debug(f"No barbs in latest column for {site}")
        return None

    heights = np.array(heights)
    directions = np.array(directions, dtype=float)
    speeds = np.array(speeds, dtype=float)

    # sort ascending by height
    order = np.argsort(heights)
    heights = heights[order]
    directions = directions[order]
    speeds = speeds[order]

    return VADProfile(
        timestamp=timestamp,
        site=site,
        heights_m=heights,
        wind_dir=directions,
        wind_spd=speeds,
    )


def fetch_vad_profile(site: str, filename: str, date_str: str) -> Optional[VADProfile]:
    """Fetch and parse a single NVW file from THREDDS.

    Args:
        site: 3-letter radar ID (e.g. "TLX")
        filename: Exact NVW filename from the catalog
        date_str: Date folder, e.g. "20260412"

    Returns:
        VADProfile if successful, None otherwise
    """
    url = f"{THREDDS_FILE}/{site}/{date_str}/{filename}"
    try:
        log.debug(f"Fetching VAD: {url}")
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.debug(f"Failed to fetch {filename}: {e}")
        return None

    # derive timestamp from filename
    m = _FNAME_RE.search(filename)
    if not m:
        return None
    hhmm = m.group(1)
    ts = datetime.strptime(f"{date_str}{hhmm}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)

    try:
        f = Level3File(BytesIO(resp.content))
        return _parse_nvw_product(f, site, ts)
    except Exception as e:
        log.error(f"Error parsing {filename}: {e}", exc_info=True)
        return None


def fetch_recent_vads(site: str, count: int = 12) -> VADSet:
    """Fetch the most recent VAD profiles for a site.

    Queries the THREDDS catalog to discover available NVW files, then
    downloads and parses up to *count* of them (newest first).

    Args:
        site: Radar site identifier (e.g. "KTLX" or "TLX")
        count: Maximum number of profiles to return (default 12)

    Returns:
        VADSet containing successfully fetched profiles
    """
    site = _normalize_site(site)
    now = datetime.now(timezone.utc)
    profiles: list[VADProfile] = []

    # check today and (if needed) yesterday to get enough files
    for day_offset in range(2):
        day = now - timedelta(days=day_offset)
        date_str = day.strftime("%Y%m%d")
        filenames = _list_nvw_files(site, date_str)

        for fname in filenames:
            if len(profiles) >= count:
                break
            profile = fetch_vad_profile(site, fname, date_str)
            if profile:
                profiles.append(profile)

        if len(profiles) >= count:
            break

    log.info(f"Fetched {len(profiles)}/{count} VAD profiles for {site}")
    return VADSet(profiles=profiles)
