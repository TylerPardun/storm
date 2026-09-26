import json
import os
from datetime import date

import pytest

from core import workspace as ws
from core.workspace import Workspace, atomic_write_text, clean_name, list_workspaces

DAY = date(2024, 4, 27)


def test_names_are_safe_folder_names():
    assert clean_name("  Tyler/../x: y  ") == "Tyler..x y"
    assert clean_name("...") == ""
    assert Workspace("", root=None).name == ws.DEFAULT_WORKSPACE


def test_default_workspace_is_always_offered(tmp_path):
    assert list_workspaces(tmp_path / "none") == ["My work"]
    (tmp_path / "Alpha").mkdir()
    assert list_workspaces(tmp_path) == ["My work", "Alpha"]


def test_tracks_listed_even_without_a_manifest_and_missing_files_dropped(tmp_path):
    w = Workspace("A", root=tmp_path)
    folder = w.case_dir(DAY)
    folder.mkdir(parents=True)
    (folder / "orphan.csv").write_text("time,lat,lon\n")
    os.utime(folder / "orphan.csv", (1_600_000_000, 1_600_000_000))   # edited in 2020
    assert [t["path"].name for t in w.tracks(DAY)] == ["orphan.csv"]

    (folder / "kept.csv").write_text("x")
    (folder / "gone.csv").write_text("x")
    w.record_track(DAY, folder / "gone.csv", 3)
    (folder / "gone.csv").unlink()
    w.record_track(DAY, folder / "kept.csv", 5, origin="/elsewhere/kept.csv")
    manifest = json.loads((folder / "manifest.json").read_text())
    assert [t["file"] for t in manifest["tracks"]] == ["kept.csv"]
    assert manifest["tracks"][0]["origin"] == "/elsewhere/kept.csv"
    assert w.tracks(DAY)[0]["path"].name == "kept.csv"          # most recent first


def test_contains_only_its_own_folder(tmp_path):
    a, b = Workspace("A", root=tmp_path), Workspace("AB", root=tmp_path)
    assert a.contains(a.case_dir(DAY) / "t.csv")
    assert not a.contains(b.case_dir(DAY) / "t.csv")


def test_a_failed_write_leaves_the_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "t.csv"
    atomic_write_text(target, "old")
    monkeypatch.setattr(ws.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        atomic_write_text(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["t.csv"]   # temp file cleaned up
