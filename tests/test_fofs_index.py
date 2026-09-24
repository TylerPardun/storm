"""The FOFS file index follows whatever layout THREDDS publishes."""

from datetime import date

from archive import fofs_index
from archive.fofs_index import crawl, get_index, vehicle_for_path

_NS = 'xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0" xmlns:xlink="http://www.w3.org/1999/xlink"'


def _catalog(refs=(), datasets=()):
    body = "".join(f'<catalogRef xlink:href="{r}" xlink:title="x" name="x"/>' for r in refs)
    body += "".join(f'<dataset name="{n}" urlPath="{p}"><date type="modified">2026-09-01T00:00:00Z</date></dataset>'
                    for n, p in datasets)
    return f'<catalog {_NS}><dataset name="d">{body}</dataset></catalog>'.encode()


_TREE = {
    "FOFS/Mobile-Mesonet/catalog.xml": _catalog(refs=["data/catalog.xml", "logger/catalog.xml"]),
    "FOFS/Mobile-Mesonet/logger/catalog.xml": _catalog(datasets=[("code.CR6", "FOFS/Mobile-Mesonet/logger/code.CR6")]),
    "FOFS/Mobile-Mesonet/data/catalog.xml": _catalog(refs=["probe1/catalog.xml", "_legacy_data/catalog.xml", "newtruck/catalog.xml"]),
    "FOFS/Mobile-Mesonet/data/probe1/catalog.xml": _catalog(refs=["raw/catalog.xml"]),
    "FOFS/Mobile-Mesonet/data/probe1/raw/catalog.xml": _catalog(datasets=[
        ("20240427.txt", "FOFS/Mobile-Mesonet/data/probe1/raw/20240427.txt")]),
    "FOFS/Mobile-Mesonet/data/_legacy_data/catalog.xml": _catalog(refs=["probe1/catalog.xml"]),
    "FOFS/Mobile-Mesonet/data/_legacy_data/probe1/catalog.xml": _catalog(datasets=[
        ("20230601.txt", "FOFS/Mobile-Mesonet/data/_legacy_data/probe1/20230601.txt")]),
    "FOFS/Mobile-Mesonet/data/newtruck/catalog.xml": _catalog(refs=["raw/catalog.xml"]),
    "FOFS/Mobile-Mesonet/data/newtruck/raw/catalog.xml": _catalog(datasets=[
        ("20250605.txt", "FOFS/Mobile-Mesonet/data/newtruck/raw/20250605.txt")]),
}


def test_crawl_finds_every_dated_file_and_its_vehicle_in_any_layout():
    files = crawl(_TREE.get)

    assert {(f.vehicle, f.date, f.path.rsplit("/", 2)[-2]) for f in files} == {
        ("probe1", date(2024, 4, 27), "raw"),
        ("probe1", date(2023, 6, 1), "probe1"),
        ("newtruck", date(2025, 6, 5), "raw"),
    }


def test_vehicle_comes_from_the_folder_structure():
    assert vehicle_for_path("FOFS/Mobile-Mesonet/data/probe1/qc_v2/20240427.txt") == "probe1"
    assert vehicle_for_path("FOFS/Mobile-Mesonet/data/2025/Probe_2/raw/20250605.txt") == "probe2"
    assert vehicle_for_path("FOFS/Mobile-Mesonet/data/_legacy_data/noxp_scout/20150529.txt") == "noxp_scout"
    assert vehicle_for_path("FOFS/Mobile-Mesonet/Hurricane_Ian_obs.txt") is None


def test_index_is_cached_and_an_empty_or_failed_crawl_never_replaces_it(tmp_path):
    # conftest stubs get_index out for every test; exercise the real one
    import importlib
    module = importlib.reload(fofs_index)
    cache = tmp_path / "index.json"
    clock = [1000.0]

    first = module.get_index(_TREE.get, cache_path=cache, now=lambda: clock[0])
    assert len(first.files) == 3 and cache.exists()

    clock[0] += module.CACHE_TTL_S + 1
    module._current = None
    stale = module.get_index(lambda path: None, cache_path=cache, now=lambda: clock[0])   # catalog "gone"
    assert stale is not None and len(stale.files) == 3

    def broken(path):
        raise TimeoutError("waf")
    module._current = None
    assert len(module.get_index(broken, cache_path=cache, now=lambda: clock[0]).files) == 3
