import json
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
