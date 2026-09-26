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
    assert manifest["case"] == CASE and manifest["format_version"] == 1
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
