"""Download caches stay under their cap, dropping the least recently used files."""
import os

from archive.fetchers.noxp_archive_fetcher import prune_download_cache


def _download(folder, name, size, age_s):
    data = folder / f"{name}.cdf"
    data.write_bytes(b"x" * size)
    (folder / f"{name}.cdf.json").write_text("{}")
    stamp = 1_700_000_000 - age_s
    os.utime(data, (stamp, stamp))
    return data


def test_oldest_downloads_go_first_until_under_the_cap(tmp_path):
    old = _download(tmp_path, "old", 4000, age_s=300)
    middle = _download(tmp_path, "middle", 4000, age_s=200)
    recent = _download(tmp_path, "recent", 4000, age_s=100)
    index = tmp_path / "noxp-asset-index.json"
    index.write_text("{}")                                        # not a download: never removed
    freed = prune_download_cache(tmp_path, max_bytes=9000)
    assert not old.exists() and not (tmp_path / "old.cdf.json").exists()
    assert middle.exists() and recent.exists() and index.exists()
    assert freed >= 4000


def test_under_the_cap_nothing_is_removed(tmp_path):
    kept = _download(tmp_path, "a", 1000, age_s=10)
    assert prune_download_cache(tmp_path, max_bytes=10_000) == 0 and kept.exists()
    assert prune_download_cache(tmp_path / "missing", max_bytes=0) == 0
