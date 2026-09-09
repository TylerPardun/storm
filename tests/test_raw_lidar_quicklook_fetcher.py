from datetime import date, datetime, timezone

from archive.fetchers import raw_lidar_quicklook_fetcher as rlq
from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset


def test_do_fetch_unions_assets_across_every_known_source(monkeypatch):
    calls = []

    def fake_discover(source, day):
        calls.append((source.platform_id, day))
        if source is KNOWN_RAW_LIDAR_SOURCES[0]:
            return [LidarAsset(source, 'x.20260517.000000.cdf', 'catalog')]
        return []

    monkeypatch.setattr(rlq, 'discover_raw_lidar', fake_discover)
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    received = []
    fetcher.assets_ready.connect(received.append)
    fetcher._do_fetch(datetime(2026, 5, 17, tzinfo=timezone.utc))
    assert len(calls) == len(KNOWN_RAW_LIDAR_SOURCES)
    assert all(day == date(2026, 5, 17) for _, day in calls)
    assert received[0] == {KNOWN_RAW_LIDAR_SOURCES[0].platform_id: [LidarAsset(KNOWN_RAW_LIDAR_SOURCES[0], 'x.20260517.000000.cdf', 'catalog')]}


def test_do_fetch_emits_error_only_when_nothing_was_found_at_all(monkeypatch):
    def failing_discover(source, day):
        raise TimeoutError('down')
    monkeypatch.setattr(rlq, 'discover_raw_lidar', failing_discover)
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    errors, assets_events = [], []
    fetcher.error.connect(errors.append)
    fetcher.assets_ready.connect(assets_events.append)
    fetcher._do_fetch(datetime(2026, 5, 17, tzinfo=timezone.utc))
    assert errors and 'down' in errors[0]
    assert assets_events == [{}]


def test_do_load_emits_rays_ready(monkeypatch):
    sentinel_rays = object()
    captured = {}

    def fake_load(asset, cache_dir):
        captured['asset'] = asset
        captured['cache_dir'] = cache_dir
        return sentinel_rays

    monkeypatch.setattr(rlq, 'load_raw_lidar', fake_load)
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    received = []
    fetcher.rays_ready.connect(lambda pid, rays: received.append((pid, rays)))
    asset = LidarAsset(KNOWN_RAW_LIDAR_SOURCES[0], 'x.20260517.000000.cdf', 'catalog')
    fetcher._do_load('DLTRUCK1-DL1-CSM', asset)
    assert received == [('DLTRUCK1-DL1-CSM', sentinel_rays)]
    assert captured['asset'] is asset
    assert captured['cache_dir'] == rlq._RAW_LIDAR_CACHE_DIR


def test_do_load_emits_error_on_failure(monkeypatch):
    def failing_load(asset, cache_dir):
        raise ValueError('bad file')
    monkeypatch.setattr(rlq, 'load_raw_lidar', failing_load)
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    errors, loaded = [], []
    fetcher.error.connect(errors.append)
    fetcher.rays_ready.connect(lambda *a: loaded.append(a))
    asset = LidarAsset(KNOWN_RAW_LIDAR_SOURCES[0], 'x.20260517.000000.cdf', 'catalog')
    fetcher._do_load('DLTRUCK1-DL1-CSM', asset)
    assert loaded == []
    assert 'bad file' in errors[0]


def test_fetch_re_entrancy_guard_blocks_a_second_call_while_busy():
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    fetcher._busy = True
    assert fetcher.fetch(datetime(2026, 5, 17, tzinfo=timezone.utc)) is False
