import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("import_mesoview_tracks", ROOT / "scripts" / "import_mesoview_tracks.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def _write(folder, name, first_time):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(f"point_id,time,lat,lon,case_id\n1,{first_time},35,-98,{name[:3].rstrip('_')}\n")


def test_tracks_are_filed_by_session_date_with_their_category(tmp_path, monkeypatch):
    source = tmp_path / "mesoview"
    _write(source / "tornadic", "T10_20190528_2200_autosave_edited.csv", "2019-05-28 22:00:00")
    _write(source / "nontornadic", "N11_20220610_0114_autosave_edited.csv", "2022-06-10 01:14:00")
    monkeypatch.setattr("core.workspace.workspaces_root", lambda: tmp_path / "tracks")
    monkeypatch.setattr(sys, "argv", ["import", str(source)])
    assert importer.main() == 0
    shared = tmp_path / "tracks" / "MESO-VIEW"
    assert (shared / "20190528" / "T10_20190528_2200_autosave_edited.csv").exists()
    assert (shared / "20220609" / "N11_20220610_0114_autosave_edited.csv").exists()   # before 12Z: previous evening
    entry = json.loads((shared / "20220609" / "manifest.json").read_text())["tracks"][0]
    assert (entry["case_id"], entry["category"]) == ("N11", "nontornadic")

    before = (shared / "20190528" / "manifest.json").read_text()
    assert importer.main() == 0                                  # re-running changes nothing
    assert (shared / "20190528" / "manifest.json").read_text() == before
