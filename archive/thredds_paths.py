"""Where STORM's data lives on THREDDS: every host, folder and datastream name
in one place.

When a THREDDS tree is reorganized (a folder renamed or moved, a new
datastream), change it here; every fetcher builds its URLs from this file.
Run `python scripts/check_data_sources.py` to see which of these locations
still answer, and, for any that don't, what their parent folder now holds.

Paths are relative to a THREDDS server's root, as they appear in its URLs:
    <host>/catalog/<path>/catalog.html      a folder's listing
    <host>/fileServer/<path>/<file>         a file
"""
from __future__ import annotations

# ---- servers -------------------------------------------------------------------
NSSL = "https://data.nssl.noaa.gov/thredds"      # NSSL field data (FOFS, CLAMPS, NOXP)
UCAR = "https://thredds.ucar.edu/thredds"        # Unidata: live NEXRAD Level 3, VAD
THREDDS_NS = "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"   # catalog.xml namespace


def catalog_root(host: str = NSSL) -> str:
    """e.g. https://data.nssl.noaa.gov/thredds/catalog"""
    return f"{host}/catalog"


def file_root(host: str = NSSL) -> str:
    """e.g. https://data.nssl.noaa.gov/thredds/fileServer"""
    return f"{host}/fileServer"


def catalog_url(path: str, host: str = NSSL, page: str = "catalog.html") -> str:
    """A folder's listing: <host>/catalog/<path>/catalog.html (or catalog.xml)."""
    return f"{catalog_root(host)}/{path.strip('/')}/{page}"


def file_url(path: str, host: str = NSSL) -> str:
    """A file: <host>/fileServer/<path>."""
    return f"{file_root(host)}/{path.strip('/')}"


# ---- FOFS: mobile mesonet and the field-coordination recordings -------------------
FOFS_MESONET = "FOFS/Mobile-Mesonet"                      # crawled whole (archive/fofs_index.py)
FOFS_MESONET_DATA = f"{FOFS_MESONET}/data"               # <vehicle>/raw/<YYYYMMDD>.txt
FOFS_ANNOTATIONS = "FOFS/Storm/annotations"              # recorded MQTT: vehicles, annotations, sectors


# ---- FRDD/CLAMPS: the two CLAMPS trailers and the lidar truck ---------------------
CLAMPS = "FRDD/CLAMPS"
DLTRUCK1 = "dltruck/dltruck1"                            # the NSSL lidar truck
CLAMPS1 = "clamps/clamps1"
CLAMPS2 = "clamps/clamps2"


def clamps_ingested(platform_dir: str, datastream: str) -> str:
    """Instrument files as recorded: FRDD/CLAMPS/<platform>/ingested/<datastream>."""
    return f"{CLAMPS}/{platform_dir}/ingested/{datastream}"


def clamps_processed(platform_dir: str, datastream: str) -> str:
    """Retrievals (winds, TROPoe): FRDD/CLAMPS/<platform>/processed/<datastream>."""
    return f"{CLAMPS}/{platform_dir}/processed/{datastream}"


# Lidar wind profiles (processed): (STORM id, platform folder, datastream)
CLAMPS_WIND_STREAMS = (
    ("DLTRUCK1-DL1-VAD", DLTRUCK1, "dltruckdlvadDL1.c1"),
    ("DLTRUCK1-DL2-VAD", DLTRUCK1, "dltruckdlvadDL2.c1"),
    ("DLTRUCK1-DL1-CSMWINDS", DLTRUCK1, "dltruckdlcsmwindsDL1.c1"),
    ("DLTRUCK1-DL2-CSMWINDS", DLTRUCK1, "dltruckdlcsmwindsDL2.c1"),
    ("CLAMPS1-VAD", CLAMPS1, "clampsdlvadC1.c1"),
    ("CLAMPS2-VAD", CLAMPS2, "clampsdlvadC2.c1"),
)

# TROPoe retrievals (processed): (STORM id, platform folder, unit suffix). The
# datastream is clampstropoe10.<variant>.<unit>, variants tried in this order.
CLAMPS_TROPOE_PLATFORMS = (
    ("CLAMPS1", CLAMPS1, "C1"),
    ("CLAMPS2", CLAMPS2, "C2"),
)
CLAMPS_TROPOE_VARIANTS = (   # in scientific preference order
    "aeri_mwr.v2", "aeri_mwr.v1", "aeri.v2", "aeri.v1", "mwr.v2", "mwr.v1",
)


def clamps_tropoe_stream(variant: str, unit: str) -> str:
    return f"clampstropoe10.{variant}.{unit}"


# Surface met (ingested): (STORM id, platform folder, datastream, kind, from the MWR)
CLAMPS_SURFACE_STREAMS = (
    ("CLAMPS2", CLAMPS2, "clampsmetC2.a1", "met_tower", False),
    ("CLAMPS1", CLAMPS1, "clampsmwrC1.a1", "mwr", True),
    ("CLAMPS2", CLAMPS2, "clampsmwrC2.a1", "mwr", True),
)

# Radiosondes launched from the lidar truck (ingested)
SONDE_PLATFORM = DLTRUCK1
SONDE_STREAM = "dltruckdlsonderawDL1.b1"

# Raw Doppler lidar scans (ingested), PPI-type files only:
# datastream = <prefix>dl<product><unit>.b1, e.g. dltruckdlppiDL1.b1
RAW_LIDAR_PRODUCTS = ("csm", "ppi")
RAW_LIDAR_PLATFORMS = (
    # (STORM id, instrument, platform folder, datastream prefix, unit, mobile)
    ("DLTRUCK1-DL1", "DLTRUCK1", DLTRUCK1, "dltruck", "DL1", True),
    ("DLTRUCK1-DL2", "DLTRUCK1", DLTRUCK1, "dltruck", "DL2", True),
    ("CLAMPS1", "CLAMPS1", CLAMPS1, "clamps", "C1", False),
    ("CLAMPS2", "CLAMPS2", CLAMPS2, "clamps", "C2", False),
)


def raw_lidar_stream(prefix: str, product: str, unit: str) -> str:
    return f"{prefix}dl{product}{unit}.b1"


# ---- CopterSonde (OU), only under the PERiLS campaign folders ------------------------
# Checked 2026-09: FRDD/UAS, FRDD/CopterSonde, FRDD/CLAMPS/CopterSonde,
# FRDD/CLAMPS/campaigns/TORUS/CopterSonde and RRDD/CopterSonde are all 404.
PERILS = f"{CLAMPS}/campaigns/PERiLS"
PERILS_IOPS = (
    ("2022", "IOP1"), ("2022", "IOP2"), ("2022", "IOP4"),
    ("2023", "IOP1"), ("2023", "IOP2"), ("2023", "IOP3"), ("2023", "IOP4"), ("2023", "IOP5"),
)


def coptersonde_iop(year: str, iop: str) -> str:
    return f"{PERILS}/{year}/CopterSonde/v1/{iop}"


# ---- RRDD/NOXP: the NOXP mobile radar -------------------------------------------------
# Ten top-level branches (confirmed 2026-09-08/09), each crawled on its own.
# real_time_test is test content and deliberately left out.
NOXP = "RRDD/NOXP"
NOXP_CAMPAIGNS = {
    "2010": f"{NOXP}/2010",
    "2011": f"{NOXP}/2011",
    "2013": f"{NOXP}/2013",
    "2015": f"{NOXP}/2015",
    "2022": f"{NOXP}/2022",
    "Colorado": f"{NOXP}/Colorado",
    "Netcdf": f"{NOXP}/Netcdf",
    "Reeves": f"{NOXP}/Reeves",
    "VORTEX2 2009": f"{NOXP}/Vortex/2009",
    "VORTEX2 2010": f"{NOXP}/Vortex/2010",
}


# ---- UCAR (live mode) ---------------------------------------------------------------
UCAR_NEXRAD_LEVEL3 = "nexrad/level3"                    # <product>/<site>/<YYYYMMDD>/


def all_folders() -> list[tuple[str, str, str, bool]]:
    """(what, host, path, required) for every folder STORM reads, for the
    checker (scripts/check_data_sources.py) and the tests. TROPoe variants
    aren't required: each trailer has only some, and the fetcher takes the
    first that exists."""
    out = [
        ("FOFS mobile mesonet", NSSL, FOFS_MESONET_DATA, True),
        ("FOFS recordings (vehicles, annotations)", NSSL, FOFS_ANNOTATIONS, True),
    ]
    out += [(f"lidar winds {sid}", NSSL, clamps_processed(d, s), True) for sid, d, s in CLAMPS_WIND_STREAMS]
    out += [(f"TROPoe {sid} {v}", NSSL, clamps_processed(d, clamps_tropoe_stream(v, u)), False)
            for sid, d, u in CLAMPS_TROPOE_PLATFORMS for v in CLAMPS_TROPOE_VARIANTS]
    out += [(f"surface {sid} {kind}", NSSL, clamps_ingested(d, s), True) for sid, d, s, kind, _ in CLAMPS_SURFACE_STREAMS]
    out.append(("lidar truck sondes", NSSL, clamps_ingested(SONDE_PLATFORM, SONDE_STREAM), True))
    out += [(f"raw lidar {sid} {p.upper()}", NSSL, clamps_ingested(d, raw_lidar_stream(prefix, p, unit)), True)
            for sid, _i, d, prefix, unit, _m in RAW_LIDAR_PLATFORMS for p in RAW_LIDAR_PRODUCTS]
    out += [(f"CopterSonde PERiLS {y} {iop}", NSSL, coptersonde_iop(y, iop), True) for y, iop in PERILS_IOPS]
    out += [(f"NOXP {name}", NSSL, path, True) for name, path in NOXP_CAMPAIGNS.items()]
    out.append(("UCAR NEXRAD Level 3 (live)", UCAR, UCAR_NEXRAD_LEVEL3, True))
    return out
