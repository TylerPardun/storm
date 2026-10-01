import sqlite3

import ui.map.html as html
from core import mbtiles


def test_local_tiles_are_used_when_present(tmp_path, monkeypatch):
    path = tmp_path / "storm.mbtiles"
    sqlite3.connect(path).execute("create table metadata (name text, value text)").connection.commit()
    monkeypatch.setattr(html, "TILES_PATH", str(path))
    page = html.build_map_html()
    assert html.basemap_source() == "local"
    assert 'tiles: ["storm://app/tiles/{z}/{x}/{y}.pbf"]' in page and html.ONLINE_TILEJSON not in page


def test_missing_or_empty_tiles_fall_back_to_the_online_map(tmp_path, monkeypatch):
    for path in (tmp_path / "absent.mbtiles", tmp_path / "empty.mbtiles"):
        if path.name.startswith("empty"):
            path.touch()
        monkeypatch.setattr(html, "TILES_PATH", str(path))
        page = html.build_map_html()
        assert html.basemap_source() == "online"
        assert f'url: "{html.ONLINE_TILEJSON}"' in page and "__TILE_URL__" not in page


def test_looking_up_bounds_never_creates_a_missing_tiles_file(tmp_path):
    path = tmp_path / "storm.mbtiles"
    assert mbtiles.bounds(str(path)) is None
    assert not path.exists()          # sqlite3.connect() would have created it


def test_bounds_are_read_from_the_metadata(tmp_path):
    path = tmp_path / "storm.mbtiles"
    conn = sqlite3.connect(path)
    conn.execute("create table metadata (name text, value text)")
    conn.execute("insert into metadata values ('bounds', '-116,28,-82,49')")
    conn.commit()
    conn.close()
    assert mbtiles.bounds(str(path)) == (-116.0, 28.0, -82.0, 49.0)
