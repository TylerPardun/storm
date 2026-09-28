import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("import_mesoview_tracks", ROOT / "scripts" / "import_mesoview_tracks.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def _write(folder, name, first_time, last_time="2019-05-28 22:30:00"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(f"point_id,time,lat,lon,case_id\n1,{first_time},35,-98,{name[:3].rstrip('_')}\n"
                               f"2,{last_time},35.1,-97.9,{name[:3].rstrip('_')}\n")


def test_tracks_are_named_like_storms_own_and_filed_by_session_date(tmp_path, monkeypatch):
    source = tmp_path / "mesoview"
    _write(source / "tornadic", "T10_20190528_2200_autosave_edited.csv", "2019-05-28 22:03:00")
    _write(source / "nontornadic", "N11_20220610_0114_autosave_edited.csv", "2022-06-10 01:14:00",
           "2022-06-10 01:40:00")
    monkeypatch.setattr("core.workspace.workspaces_root", lambda: tmp_path / "tracks")
    monkeypatch.setattr(sys, "argv", ["import", str(source)])
    assert importer.main() == 0
    shared = tmp_path / "tracks" / "Pardun_Tracks"
    assert sorted(p.name for p in shared.glob("*.csv")) == [
        "storm_20190528_2203_track.csv",                         # from the first point, no case ID
        "storm_20220610_0114_track.csv"]
    entries = {t["file"]: t for t in json.loads((shared / "manifest.json").read_text())["tracks"]}
    assert entries["storm_20220610_0114_track.csv"]["session_date"] == "2022-06-09"   # before 12Z: previous evening
    assert entries["storm_20190528_2203_track.csv"]["session_date"] == "2019-05-28"

    before = (shared / "manifest.json").read_text()
    assert importer.main() == 0                                  # re-running changes nothing
    assert (shared / "manifest.json").read_text() == before

    _write(source / "tornadic", "T10_20190528_2200_autosave_edited.csv", "2019-05-28 22:01:00")
    assert importer.main() == 0                                  # an earlier first point: still the same storm
    assert len(list(shared.glob("*.csv"))) == 2
    text = (shared / "storm_20190528_2203_track.csv").read_text()
    assert "22:01:00" in text and "case_id" not in text          # only STORM's own columns
