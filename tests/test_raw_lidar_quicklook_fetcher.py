from datetime import date, datetime, timezone

from archive.fetchers import raw_lidar_quicklook_fetcher as rlq
from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset


def test_do_fetch_unions_assets_across_every_known_source(monkeypatch):
    calls = []

    def fake_discover(source, day):
        calls.append((source.platform_id, day))
        if source is KNOWN_RAW_LIDAR_SOURCES[0]:
            return [LidarAsset(source, f'x.{day:%Y%m%d}.000000.cdf', 'catalog')]
        return []

    monkeypatch.setattr(rlq, 'discover_raw_lidar', fake_discover)
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    received = []
    fetcher.assets_ready.connect(received.append)
    fetcher._do_fetch(datetime(2026, 5, 17, tzinfo=timezone.utc))
    # the session day and the next morning (the session runs to 06Z)
    assert len(calls) == 2 * len(KNOWN_RAW_LIDAR_SOURCES)
    assert {day for _, day in calls} == {date(2026, 5, 17), date(2026, 5, 18)}
    # vertical stares (fp) and RHIs (other) are never even looked for
    assert not any(pid.endswith(("-FP", "-OTHER")) for pid, _ in calls)
    first = KNOWN_RAW_LIDAR_SOURCES[0]
    assert received[0] == {first.platform_id: [LidarAsset(first, 'x.20260517.000000.cdf', 'catalog'),
                                               LidarAsset(first, 'x.20260518.000000.cdf', 'catalog')]}


def test_do_fetch_preserves_unresolved_sources(monkeypatch):
    def failing_discover(source, day):
        raise TimeoutError('down')
    monkeypatch.setattr(rlq, 'discover_raw_lidar', failing_discover)
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    errors, assets_events = [], []
    fetcher.error.connect(errors.append)
    fetcher.assets_ready.connect(assets_events.append)
    fetcher._do_fetch(datetime(2026, 5, 17, tzinfo=timezone.utc))
    assert errors and 'down' in errors[0]
    assert assets_events == [{s.platform_id: None for s in KNOWN_RAW_LIDAR_SOURCES}]


def test_discovery_guard_blocks_a_second_discovery_while_busy():
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    fetcher._busy = True
    assert fetcher.fetch(datetime(2026, 5, 17, tzinfo=timezone.utc)) is False


def test_partial_success_does_not_hide_discovery_errors(monkeypatch):
    source = KNOWN_RAW_LIDAR_SOURCES[0]
    asset = LidarAsset(source, 'a.cdf', 'catalog')
    def discover(candidate, day):
        if candidate == source:
            return [asset] if day == date(2026, 6, 10) else []
        raise TimeoutError('down')
    monkeypatch.setattr(rlq, 'discover_raw_lidar', discover)
    fetcher = rlq.ArchiveRawLidarQuicklookFetcher()
    errors, results = [], []
    fetcher.error.connect(errors.append)
    fetcher.assets_ready.connect(results.append)
    fetcher._do_fetch(date(2026, 6, 10))
    assert errors
    assert results[0][source.platform_id] == [asset]
    assert results[0][KNOWN_RAW_LIDAR_SOURCES[1].platform_id] is None
