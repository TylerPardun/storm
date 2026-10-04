import json
from datetime import datetime, timezone
import zipfile

import pytest

from core import case_package, provenance

CASE = {"session_date": "2024-04-27", "archive_time": "2024-04-27T20:00:00Z",
        "window_start": "2024-04-27T00:00:00Z", "window_end": "2024-04-28T03:02:19Z",
        "clock_time": "2024-04-28T01:15:00Z", "radar_station": "KFDR", "radar_product": "velocity",
        "radar_tilt_index": 1, "velocity": {"dealias": True, "storm_relative": False}}


def test_round_trip_with_tracks_provenance_and_readme(tmp_path):
    track = tmp_path / "storm_20240427_2000_track.csv"
    track.write_text("point_id,time,lat,lon\n1,2024-04-27 20:00:00,35,-98\n")
    provenance.reset()
    provenance.record("radar", "https://s3/KFDR20240427_200445_V06", b"volume bytes")
    provenance.record("radar", "https://s3/KFDR20240427_200445_V06", b"volume bytes")   # once only
    path = case_package.build(tmp_path / "case.zip", case=CASE, tracks=[track], sources=provenance.snapshot())

    manifest, tracks = case_package.read(path)
    assert manifest["case"] == CASE and manifest["format_version"] == 2
    assert list(tracks) == [track.name] and tracks[track.name] == track.read_bytes()
    (source,) = manifest["sources"]
    import hashlib
    assert source["sha256"] == hashlib.sha256(b"volume bytes").hexdigest()
    assert source["bytes"] == len(b"volume bytes")
    readme = zipfile.ZipFile(path).read("README.txt").decode()
    assert "Archive date:   2024-04-27" in readme and "KFDR" in readme and "sha256" in readme
    assert not list(tmp_path.glob(".*part"))                 # written atomically


@pytest.mark.parametrize("contents, message", [
    ({"package.json": json.dumps({"format": "other"})}, "not a STORM case package"),
    ({"package.json": json.dumps({"format": case_package.FORMAT, "format_version": 99})}, "newer STORM"),
    ({"package.json": json.dumps({"format": case_package.FORMAT, "format_version": 1,
                                  "tracks": ["../evil.csv"]}), "../evil.csv": "x"}, "unexpected entry"),
    ({"other.txt": "x"}, "not a readable"),
])
def test_bad_packages_are_refused(tmp_path, contents, message):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in contents.items():
            z.writestr(name, data)
    with pytest.raises(ValueError, match=message):
        case_package.read(path)


def test_not_a_zip_is_refused(tmp_path):
    path = tmp_path / "x.zip"
    path.write_text("hello")
    with pytest.raises(ValueError, match="not a readable"):
        case_package.read(path)


def test_version_reports_the_checkout():
    version = case_package.storm_version()
    assert version["version"]                                 # from VERSION
    assert len(version.get("commit", "x" * 40)) == 40


def _sources(provenance_module):
    provenance_module.reset()
    provenance_module.record("radar", "https://s3/KFDR_V06", b"R" * 3_000_000)   # large: re-downloaded
    provenance_module.record("mesonet", "https://thredds/p1/20240427.txt", b"mesonet rows")
    provenance_module.record("satellite", "https://goes/frame.nc", b"sat")
    return provenance_module.snapshot()


def test_chosen_data_types_are_packed_and_served_back(tmp_path, monkeypatch):
    from core import package_sources
    sources = _sources(provenance)
    downloads = []

    def fetch(url):
        downloads.append(url)
        return provenance.kept_bytes(url) or b"R" * 3_000_000
    path = case_package.build(tmp_path / "case.zip", case=CASE, tracks=[], sources=sources,
                              include_kinds={"radar", "mesonet"}, fetch=fetch)
    manifest, _ = case_package.read(path)
    assert {d["kind"] for d in manifest["data"]} == {"radar", "mesonet"}
    assert [s["included"] for s in manifest["sources"] if s["kind"] == "satellite"] == [False]
    readme = zipfile.ZipFile(path).read("README.txt").decode()
    assert "Not included (downloaded from their sources when the package is opened): Satellite imagery" in readme

    package_sources.activate(path, manifest["data"])
    try:
        assert package_sources.get("https://thredds/p1/20240427.txt") == b"mesonet rows"
        assert package_sources.get("https://goes/frame.nc") is None          # left out: network instead
        assert package_sources.served()["mesonet"] == 1
    finally:
        package_sources.deactivate()


def test_a_file_changed_upstream_is_packed_but_marked(tmp_path):
    sources = _sources(provenance)
    path = case_package.build(tmp_path / "case.zip", case=CASE, tracks=[], sources=sources,
                              include_kinds={"mesonet"}, fetch=lambda url: b"mesonet rows, reprocessed")
    manifest, _ = case_package.read(path)
    (entry,) = manifest["data"]
    assert entry["viewed_sha256"] != entry["sha256"] and manifest["data_changed_since_viewed"] == 1


def test_a_failed_download_is_reported_not_packed(tmp_path):
    sources = _sources(provenance)

    def fetch(url):
        raise OSError("offline")
    path = case_package.build(tmp_path / "case.zip", case=CASE, tracks=[], sources=sources,
                              include_kinds={"radar"}, fetch=fetch)
    manifest, _ = case_package.read(path)
    assert manifest["data"] == [] and manifest["data_not_packed"][0]["error"] == "offline"


def test_a_tampered_member_falls_back_to_the_network(tmp_path):
    from core import package_sources
    sources = _sources(provenance)
    path = case_package.build(tmp_path / "case.zip", case=CASE, tracks=[], sources=sources,
                              include_kinds={"mesonet"}, fetch=provenance.kept_bytes)
    manifest, _ = case_package.read(path)
    entry = dict(manifest["data"][0], sha256="0" * 64)
    package_sources.activate(path, [entry])
    try:
        assert package_sources.get(entry["url"]) is None
    finally:
        package_sources.deactivate()


def test_data_paths_outside_data_are_refused(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("package.json", json.dumps({"format": case_package.FORMAT, "format_version": 2,
                                               "data": [{"member": "../../etc/x", "url": "u"}]}))
    with pytest.raises(ValueError, match="unexpected entry"):
        case_package.read(path)


@pytest.mark.parametrize("url, expected", [
    ("https://s3/2024/04/27/KFDR/KFDR20240427_200445_V06", datetime(2024, 4, 27, 20, 4, 45)),
    ("https://t/CLAMPS/clamps1/clampsdlppiC1.b1/clampsdlppiC1.b1.20240427.200000.cdf", datetime(2024, 4, 27, 20)),
    ("https://t/x/DL1dlppi.20240427.203512.cdf", datetime(2024, 4, 27, 20, 35, 12)),
    ("https://s3/ABI/OR_ABI-L2-CMIPC-M6C13_G16_s20241182031171_e20241182033544_c1.nc",
     datetime(2024, 4, 27, 20, 31, 17)),
    ("https://t/probe1/qc_v2/20240427.txt", None),                          # daily
    ("https://s3/?list-type=2&prefix=2024/04/27/KFDR/KFDR20240427_20", None),  # listing
])
def test_file_start_reads_the_scan_time_from_the_name(url, expected):
    from core.case_package import file_start
    got = file_start(url)
    assert got == (expected.replace(tzinfo=timezone.utc) if expected else None)


def test_time_frame_keeps_files_covering_it(tmp_path):
    from core.case_package import in_time_frame
    frame = (datetime(2024, 4, 27, 20, 30, tzinfo=timezone.utc), datetime(2024, 4, 27, 21, tzinfo=timezone.utc))
    radar = [{"kind": "radar", "url": f"https://s3/KFDR20240427_{t}_V06"}
             for t in ("200000", "202600", "203100", "205800", "210400")]
    kept = in_time_frame(radar, frame)
    names = sorted(u.rsplit("/", 1)[1][13:19] for u in kept)
    assert names == ["202600", "203100", "205800"]       # 20:26 is on screen at 20:30; 20:00 and 21:04 aren't
    # a daily CLAMPS file stamped .000000 covers the whole day
    daily = [{"kind": "raw lidar", "url": f"https://t/clampsdlppiC2.b1.202404{d}.000000.cdf"} for d in (27, 28)]
    assert in_time_frame(daily, frame) == {daily[0]["url"]}
    # a lone radar volume long before the frame doesn't cover it
    assert not in_time_frame([{"kind": "radar", "url": "https://s3/KFDR20240427_120000_V06"}], frame)
    # daily mesonet files and anything without a frame are kept
    meso = {"kind": "mesonet", "url": "https://t/20240427.txt"}
    assert in_time_frame([meso], frame) == {meso["url"]}
    assert in_time_frame(radar, None) == {r["url"] for r in radar}


def test_a_time_frame_is_recorded_and_only_its_scans_are_packed(tmp_path):
    from core import case_package
    frame = (datetime(2024, 4, 27, 20, tzinfo=timezone.utc), datetime(2024, 4, 27, 21, tzinfo=timezone.utc))
    sources = [{"kind": "radar", "url": f"https://s3/KFDR20240427_{t}_V06", "sha256": None, "bytes": 3}
               for t in ("200445", "230000")]
    out = case_package.build(tmp_path / "c.zip", case={"session_date": "2024-04-27"}, tracks=[],
                             sources=sources, include_kinds={"radar"}, time_frame=frame,
                             fetch=lambda url: b"abc")
    manifest, _ = case_package.read(out)
    assert [d["url"].rsplit("/", 1)[1] for d in manifest["data"]] == ["KFDR20240427_200445_V06"]
    assert manifest["time_frame"] == {"start": "2024-04-27T20:00:00Z", "end": "2024-04-27T21:00:00Z"}
    assert "Time frame:     2024-04-27T20:00:00Z" in zipfile.ZipFile(out).read("README.txt").decode()


def test_identical_files_from_two_queries_are_stored_once(tmp_path):
    import warnings
    sources = [{"kind": "hazards", "url": f"https://iem/spcwatch.py?ts={t}", "sha256": None, "bytes": 2}
               for t in ("1", "2")]
    with warnings.catch_warnings():
        warnings.simplefilter("error")          # zipfile warns on a duplicate name
        out = case_package.build(tmp_path / "c.zip", case={}, tracks=[], sources=sources,
                                 include_kinds={"hazards"}, fetch=lambda url: b"{}")
    manifest, _ = case_package.read(out)
    assert len(manifest["data"]) == 2 and len({d["member"] for d in manifest["data"]}) == 1
    assert sum(n.startswith("data/") for n in zipfile.ZipFile(out).namelist()) == 1


def test_listed_files_offer_unloaded_scans_from_loaded_catalogs():
    from core.case_package import in_time_frame, listed_files
    thredds = "https://data.nssl.noaa.gov/thredds/catalog/FRDD/CLAMPS/clamps2/ingested/clampsdlppiC2.b1/catalog.html"
    s3 = "https://s3.example/?prefix=2024/04/27/KFDR/KFDR20240427_20&list-type=2"
    html = "".join(
        f'<tr><td><a href="catalog.html?dataset=FRDD/CLAMPS/clamps2/ingested/clampsdlppiC2.b1/'
        f'clampsdlppiC2.b1.202404{d}.000000.cdf"><code>x</code></a></td>'
        f'<td align="right">&nbsp;<code>338.4 Mbytes</code></td></tr>' for d in (26, 27, 28))
    xml = "".join(f"<Contents><Key>2024/04/27/KFDR/KFDR20240427_{t}</Key><Size>14000000</Size></Contents>"
                  for t in ("200445_V06", "201130_V06", "201130_V06_MDM"))
    sources = [{"kind": "raw lidar", "url": thredds}, {"kind": "radar", "url": s3},
               {"kind": "radar", "url": "https://s3.example/2024/04/27/KFDR/KFDR20240427_200445_V06"}]
    found = listed_files(sources, {thredds: html.encode(), s3: xml.encode()}.get)
    urls = {e["url"]: e for e in found}
    daily = "https://data.nssl.noaa.gov/thredds/fileServer/FRDD/CLAMPS/clamps2/ingested/clampsdlppiC2.b1/" \
            "clampsdlppiC2.b1.20240427.000000.cdf"
    assert urls[daily]["bytes"] == 338_400_000 and urls[daily]["listed"]
    assert "https://s3.example/2024/04/27/KFDR/KFDR20240427_201130_V06" in urls    # gap in a shown series
    assert not any(u.endswith("_MDM") for u in urls)                               # a series never shown
    assert not any(u.endswith("200445_V06") for u in urls)                         # already loaded
    frame = (datetime(2024, 4, 27, 20, tzinfo=timezone.utc), datetime(2024, 4, 27, 21, tzinfo=timezone.utc))
    keep = in_time_frame(sources + found, frame)
    assert daily in keep and not any("20240426" in u or "20240428" in u for u in keep)
