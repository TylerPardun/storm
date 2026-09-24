from datetime import date, datetime, timezone

from archive.fetchers.noxp_archive_fetcher import RadarAsset, RadarInventory, RadarVolume
from archive.fetchers.noxp_radar_archive_fetcher import ArchiveNoxpRadarFetcher
import numpy as np


class _FakeNoxp:
    def __init__(self, inventory=None, volume=None, raise_on_discover=None, raise_on_load=None, fully_indexed=None):
        self.inventory = inventory
        self.volume = volume
        self.raise_on_discover = raise_on_discover
        self.raise_on_load = raise_on_load
        self.discover_calls = []
        self.load_calls = []
        # Roots this fake considers fully indexed after a discover() call --
        # a real NoxpArchive derives this from the crawl outcome itself, but
        # the fake just reports whatever the test configured.
        self._fully_indexed = set(fully_indexed or ())

    def discover(self, *, target=None, budget, catalog_root):
        self.discover_calls.append((target, budget, catalog_root))
        if self.raise_on_discover:
            raise self.raise_on_discover
        return self.inventory

    def is_root_fully_indexed(self, catalog_root):
        return catalog_root in self._fully_indexed

    def load(self, asset):
        self.load_calls.append(asset)
        if self.raise_on_load:
            raise self.raise_on_load
        return self.volume


def _fetcher(fake):
    return ArchiveNoxpRadarFetcher(noxp_factory=lambda: fake)


def test_discover_queues_latest_request_while_busy():
    fetcher = ArchiveNoxpRadarFetcher(noxp_factory=lambda: _FakeNoxp(RadarInventory()))
    fetcher._busy = True
    assert fetcher.discover("NOXP-2013", "https://example.test/catalog.html", datetime(2013, 5, 31, tzinfo=timezone.utc)) is True
    assert fetcher._pending is not None


def test_do_discover_emits_assets_ready_with_the_real_assets():
    asset = RadarAsset('https://example.test/x', 'NOX130531214405.RAWETW4', 'sigmet', datetime(2013, 5, 31, 21, 44, 5, tzinfo=timezone.utc))
    fake = _FakeNoxp(inventory=RadarInventory(assets=[asset], pending=0))
    fetcher = _fetcher(fake)
    received = []
    fetcher.assets_ready.connect(lambda pid, assets: received.append((pid, assets)))
    fetcher._do_discover("NOXP-2013", "https://example.test/2013/catalog.html", datetime(2013, 5, 31, tzinfo=timezone.utc))
    assert received == [("NOXP-2013", [asset])]
    # the session day and the next morning (the session runs to 06Z)
    assert fake.discover_calls == [(date(2013, 5, 31), 40, "https://example.test/2013/catalog.html"),
                                   (date(2013, 6, 1), 40, "https://example.test/2013/catalog.html")]


def test_do_discover_keeps_next_morning_volumes_and_drops_ones_outside_the_session():
    evening = RadarAsset('https://example.test/a', 'a', 'sigmet', datetime(2013, 6, 1, 1, 30, tzinfo=timezone.utc))
    too_late = RadarAsset('https://example.test/b', 'b', 'sigmet', datetime(2013, 6, 1, 7, 0, tzinfo=timezone.utc))
    fake = _FakeNoxp(inventory=RadarInventory(assets=[evening, too_late], pending=0))
    fetcher = _fetcher(fake)
    received = []
    fetcher.assets_ready.connect(lambda pid, assets: received.append(assets))
    fetcher._do_discover("NOXP-2013", "https://example.test/2013/catalog.html", datetime(2013, 5, 31, tzinfo=timezone.utc))
    assert received == [[evening]]


def test_do_discover_still_emits_assets_ready_when_the_crawl_was_incomplete():
    # NoxpArchive.discover() catches its own request failures internally and
    # returns normally (never raises) -- even the real assets it did find
    # before running out of budget/time must still reach the caller.
    asset = RadarAsset('https://example.test/x', 'NOX130531145529.RAW8K8H', 'sigmet', datetime(2013, 5, 31, 14, 55, 29, tzinfo=timezone.utc))
    fake = _FakeNoxp(RadarInventory(assets=[asset], pending=5))
    fetcher = _fetcher(fake)
    assets_events, errors = [], []
    fetcher.assets_ready.connect(lambda *a: assets_events.append(a))
    fetcher.error.connect(errors.append)
    fetcher._do_discover("NOXP-2013", "https://example.test/2013/catalog.html", datetime(2013, 5, 31, tzinfo=timezone.utc))
    assert assets_events == [("NOXP-2013", [asset])]
    assert errors and 'incomplete' in errors[0] and 'found 1' in errors[0]
    assert 'example.test' not in errors[0]  # raw URLs stay in the log, not the on-screen status


def test_do_discover_surfaces_a_root_fetch_failure_as_zero_assets_plus_an_error():
    # A failed root-catalog fetch (e.g. transient WAF timeout under
    # concurrent archive-startup load, observed directly while wiring this
    # up) must never look identical to a genuine "no NOXP data this date" --
    # NoxpArchive.discover() reports it via inventory.errors, not a raise.
    fake = _FakeNoxp(RadarInventory(assets=[], pending=0, errors=['https://example.test: timed out']))
    fetcher = _fetcher(fake)
    assets_events, errors = [], []
    fetcher.assets_ready.connect(lambda *a: assets_events.append(a))
    fetcher.error.connect(errors.append)
    fetcher._do_discover("NOXP-2013", "https://example.test/2013/catalog.html", datetime(2013, 5, 31, tzinfo=timezone.utc))
    assert assets_events == [("NOXP-2013", [])]
    assert errors and 'incomplete' in errors[0] and 'timed out' in errors[0]


def test_do_discover_emits_no_error_when_the_crawl_genuinely_completed():
    fake = _FakeNoxp(RadarInventory(assets=[], pending=0, errors=[]))
    fetcher = _fetcher(fake)
    assets_events, errors = [], []
    fetcher.assets_ready.connect(lambda *a: assets_events.append(a))
    fetcher.error.connect(errors.append)
    fetcher._do_discover("NOXP-2013", "https://example.test/2013/catalog.html", datetime(2013, 5, 31, tzinfo=timezone.utc))
    assert assets_events == [("NOXP-2013", [])]
    assert errors == []


def test_do_discover_emits_error_and_no_assets_ready_on_failure():
    fake = _FakeNoxp(raise_on_discover=TimeoutError("timed out"))
    fetcher = _fetcher(fake)
    assets_events, errors = [], []
    fetcher.assets_ready.connect(lambda *a: assets_events.append(a))
    fetcher.error.connect(errors.append)
    fetcher._do_discover("NOXP-2013", "https://example.test/2013/catalog.html", datetime(2013, 5, 31, tzinfo=timezone.utc))
    assert assets_events == []
    assert 'timed out' in errors[0]


def test_do_load_volume_emits_volume_loaded():
    volume = RadarVolume(
        time_epoch=np.array([0.0]), range_m=np.array([1000.0]),
        azimuth_deg=np.array([0.0]), elevation_deg=np.array([0.5]),
        latitude=np.array([35.0]), longitude=np.array([-97.0]), altitude_m=np.array([400.0]),
        fields={}, sweep_start=np.array([0]), sweep_end=np.array([0]),
        scan_type='ppi', provenance={},
    )
    asset = RadarAsset('https://example.test/x', 'NOX130531214405.RAWETW4', 'sigmet', None)
    fake = _FakeNoxp(volume=volume)
    fetcher = _fetcher(fake)
    received = []
    fetcher.volume_loaded.connect(lambda pid, v: received.append((pid, v)))
    fetcher._do_load_volume("NOXP-2013", asset)
    assert received == [("NOXP-2013", volume)]
    assert fake.load_calls == [asset]


def test_do_load_volume_emits_error_on_failure():
    asset = RadarAsset('https://example.test/x', 'bad.file', 'sigmet', None)
    fake = _FakeNoxp(raise_on_load=ValueError("corrupt"))
    fetcher = _fetcher(fake)
    loaded, errors = [], []
    fetcher.volume_loaded.connect(lambda *a: loaded.append(a))
    fetcher.error.connect(errors.append)
    fetcher._do_load_volume("NOXP-2013", asset)
    assert loaded == []
    assert 'corrupt' in errors[0]


def test_noxp_instance_is_reused_across_discover_calls_not_rebuilt():
    """Regression test: the factory must be called at most once, so repeat
    discover() calls resume via the same NoxpArchive's internal catalog
    memo instead of each starting a fresh, empty-memo crawl (which made
    the "search incomplete ... try again" status a no-op)."""
    built = []

    def factory():
        fake = _FakeNoxp(inventory=RadarInventory(assets=[], pending=0))
        built.append(fake)
        return fake

    fetcher = ArchiveNoxpRadarFetcher(noxp_factory=factory)
    fetcher._do_discover("NOXP-2013", "https://example.test/catalog.html",
                          datetime(2013, 5, 31, tzinfo=timezone.utc))
    fetcher._do_discover("NOXP-2013", "https://example.test/catalog.html",
                          datetime(2013, 5, 31, tzinfo=timezone.utc))

    assert len(built) == 1
    assert len(built[0].discover_calls) == 4  # two sessions x (session day, next morning)


def test_do_index_root_calls_discover_with_no_target_and_reports_progress():
    root = "https://example.test/2013/catalog.html"
    fake = _FakeNoxp(inventory=RadarInventory(assets=[], pending=0), fully_indexed={root})
    fetcher = _fetcher(fake)
    progress = []
    fetcher.root_index_progress.connect(lambda r, done: progress.append((r, done)))
    fetcher._do_index_root(root, budget=60)
    assert fake.discover_calls == [(None, 60, root)]
    assert progress == [(root, True)]


def test_do_index_root_reports_not_yet_complete():
    root = "https://example.test/2010/catalog.html"
    fake = _FakeNoxp(inventory=RadarInventory(assets=[], pending=200), fully_indexed=set())
    fetcher = _fetcher(fake)
    progress = []
    fetcher.root_index_progress.connect(lambda r, done: progress.append((r, done)))
    fetcher._do_index_root(root, budget=60)
    assert progress == [(root, False)]


def test_try_background_index_is_a_no_op_when_the_worker_is_busy():
    """Unlike discover()/load_volume(), a background-indexing attempt must
    never occupy the single `_pending` slot -- doing so risks silently
    dropping a real foreground request queued there instead."""
    fetcher = ArchiveNoxpRadarFetcher(noxp_factory=lambda: _FakeNoxp(RadarInventory()))
    fetcher._busy = True
    started = fetcher.try_background_index("https://example.test/2013/catalog.html")
    assert started is False
    assert fetcher._pending is None


def test_try_background_index_runs_and_completes_when_idle():
    import time
    from PyQt6.QtCore import QCoreApplication
    root = "https://example.test/2013/catalog.html"
    fake = _FakeNoxp(inventory=RadarInventory(assets=[], pending=0), fully_indexed={root})
    fetcher = _fetcher(fake)
    progress = []
    fetcher.root_index_progress.connect(lambda r, done: progress.append((r, done)))

    started = fetcher.try_background_index(root, budget=60)
    assert started is True

    # The worker runs on its own thread; root_index_progress is a queued
    # cross-thread signal that only delivers while the event loop is
    # pumped, which a plain pytest run doesn't do on its own.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not progress:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    assert fake.discover_calls == [(None, 60, root)]
    assert progress == [(root, True)]
    assert fetcher._busy is False
