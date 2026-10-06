"""archive/thredds_paths.py (THREDDS) and data/endpoints.py (AWS, web APIs) are\nthe only places STORM's data addresses are written."""
import ast
import re
from datetime import date
from pathlib import Path

from archive import thredds_paths as paths

ROOT = Path(__file__).resolve().parents[1]


MAPS = {"archive/thredds_paths.py", "data/endpoints.py"}
ALLOWED = {"config.py",            # the NSSL API root (overridable) and the MQTT broker
           "setup.py"}             # the Miniforge installer link
_NAMESPACE = re.compile(r"https?://(?:www\.w3\.org|www\.unidata\.ucar\.edu/namespaces|s3\.amazonaws\.com/doc|"
                        r"www\.opengis\.net|purl\.org)")


def _code_strings(path: Path):
    """(line, text) of every string in the code itself -- not comments or docstrings."""
    tree = ast.parse(path.read_text(errors="replace"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


def test_no_data_address_is_written_outside_the_two_maps():
    # a host or THREDDS folder written anywhere else means the next move of
    # a data source has to be chased through the code again
    thredds = re.compile(r"data\.nssl\.noaa\.gov/thredds|thredds\.ucar\.edu|^(?:FRDD|RRDD|FOFS)/[\w.-]+/")
    web = re.compile(r"https?://[A-Za-z0-9]")
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", ".", "STORM.app/")) or rel in MAPS or "/." in rel:
            continue
        for line, text in _code_strings(path):
            if thredds.search(text) or (rel not in ALLOWED and web.search(text) and not _NAMESPACE.search(text)):
                offenders.append(f"{rel}:{line}: {text[:90]}")
    assert not offenders, "\n".join(offenders)


def test_the_fetchers_build_the_same_urls_as_before():
    from archive.fetchers import (clamps_sonde_archive_fetcher as so, clamps_surface_archive_fetcher as sf,
                                  clamps_wind_archive_fetcher as w, coptersonde_archive_fetcher as c,
                                  mqtt_reader as mq, noxp_archive_fetcher as nx, raw_lidar_archive_fetcher as rl,
                                  vehicle_obs_archive_fetcher as vo)
    nssl = "https://data.nssl.noaa.gov/thredds"
    assert w._source_url(w.KNOWN_CLAMPS_WIND_SOURCES[4], "20240427") == (
        f"{nssl}/fileServer/FRDD/CLAMPS/clamps/clamps1/processed/clampsdlvadC1.c1/clampsdlvadC1.c1.20240427.000000.cdf")
    assert vo._daily_url("probe1", "20240427") == f"{nssl}/fileServer/FOFS/Mobile-Mesonet/data/probe1/raw/20240427.txt"
    assert mq._THREDDS_ANNOTATIONS_ROOT == f"{nssl}/fileServer/FOFS/Storm/annotations"
    assert (nx.ROOT, nx.DATA_ROOT) == (f"{nssl}/catalog/RRDD/NOXP/", f"{nssl}/fileServer/RRDD/NOXP/")
    src = next(s for s in rl.KNOWN_RAW_LIDAR_SOURCES if s.platform_id == "CLAMPS2-PPI")
    assert src.path == "FRDD/CLAMPS/clamps/clamps2/ingested/clampsdlppiC2.b1"
    assert paths.catalog_url(paths.clamps_ingested(so._SONDE_PLATFORM_DIR, so._SONDE_DATASTREAM)) == (
        f"{nssl}/catalog/FRDD/CLAMPS/dltruck/dltruck1/ingested/dltruckdlsonderawDL1.b1/catalog.html")
    assert paths.catalog_url(paths.coptersonde_iop("2023", "IOP3")) == (
        f"{nssl}/catalog/FRDD/CLAMPS/campaigns/PERiLS/2023/CopterSonde/v1/IOP3/catalog.html")
    assert sf.KNOWN_CLAMPS_SURFACE_SOURCES[0].datastream == "clampsmetC2.a1"
    assert c.KNOWN_PERILS_IOPS[0] == ("2022", "IOP1")


def test_every_folder_the_checker_asks_for_is_distinct_and_well_formed():
    folders = paths.all_folders()
    assert len({(h, p) for _, h, p, _ in folders}) == len(folders)
    assert all(not p.startswith("/") and not p.endswith("/") and "//" not in p for _, _, p, _ in folders)
    assert {h for _, h, _, _ in folders} == {paths.NSSL, paths.UCAR}


def test_every_endpoint_the_checker_probes_is_distinct():
    from data import endpoints
    probes = endpoints.all_endpoints()
    assert len({u for _, u in probes}) == len(probes)
    assert all(u.startswith("https://") for _, u in probes)
    assert endpoints.goes_east_bucket(endpoints.GOES19_EAST_START) == "noaa-goes19"
