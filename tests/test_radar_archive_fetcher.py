import gzip
from datetime import datetime, timezone
from types import SimpleNamespace

import archive.fetchers.radar_archive_fetcher as raf
from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher

_SCAN_TIME = datetime(2013, 5, 31, 23, 9, 57, tzinfo=timezone.utc)
_PREFIX = "KTLX20130531_230957"


def _fetcher():
    return ArchiveRadarFetcher("KTLX", datetime(2013, 5, 31, tzinfo=timezone.utc))


def test_download_scan_matches_plain_v06_suffix(monkeypatch):
    fetcher = _fetcher()

    def fake_get(url, timeout=60, stream=True):
        if url.endswith("_V06"):
            return SimpleNamespace(status_code=200, content=b"RAWDATA")
        return SimpleNamespace(status_code=404, content=b"")

    monkeypatch.setattr(raf.requests, "get", fake_get)
    assert fetcher._download_scan(_SCAN_TIME) == b"RAWDATA"


def test_download_scan_tries_expected_suffixes_in_order(monkeypatch):
    # No suffix matches, so every candidate should be attempted, in order,
    # trying the uncompressed keys before the gzip-suffixed fallbacks.
    fetcher = _fetcher()
    requested = []

    def fake_get(url, timeout=60, stream=True):
        requested.append(url.split("/")[-1])
        return SimpleNamespace(status_code=404, content=b"")

    monkeypatch.setattr(raf.requests, "get", fake_get)
    assert fetcher._download_scan(_SCAN_TIME) is None
    assert requested == [
        f"{_PREFIX}_V06", f"{_PREFIX}_V03", _PREFIX,
        f"{_PREFIX}_V06.gz", f"{_PREFIX}_V03.gz", f"{_PREFIX}.gz",
    ]


def test_download_scan_falls_back_to_gzip_suffixed_key_and_decompresses(monkeypatch):
    # Regression test: the unidata-nexrad-level2 bucket was reorganized
    # (observed 2025-06-28 LastModified timestamps) to store every object
    # gzip-compressed under a ".gz"-suffixed key, e.g.
    # KTLX20130531_230957_V06.gz instead of KTLX20130531_230957_V06. The
    # plain suffixes now 404, so _download_scan must fall back to the
    # gzip-suffixed variant and transparently decompress it (S3 doesn't set
    # Content-Encoding for these objects, so requests won't do it for us).
    fetcher = _fetcher()
    raw = b"AR2V0006" + b"fake level2 payload"
    compressed = gzip.compress(raw)
    requested = []

    def fake_get(url, timeout=60, stream=True):
        name = url.split("/")[-1]
        requested.append(name)
        if name == f"{_PREFIX}_V06.gz":
            return SimpleNamespace(status_code=200, content=compressed)
        return SimpleNamespace(status_code=404, content=b"")

    monkeypatch.setattr(raf.requests, "get", fake_get)
    data = fetcher._download_scan(_SCAN_TIME)
    assert data == raw
    # confirms the uncompressed candidates were tried first, then the gzip one.
    assert requested == [
        f"{_PREFIX}_V06", f"{_PREFIX}_V03", _PREFIX, f"{_PREFIX}_V06.gz",
    ]


def test_download_scan_returns_none_when_every_suffix_404s(monkeypatch):
    fetcher = _fetcher()
    monkeypatch.setattr(
        raf.requests, "get",
        lambda url, timeout=60, stream=True: SimpleNamespace(status_code=404, content=b""),
    )
    assert fetcher._download_scan(_SCAN_TIME) is None


def test_download_scan_returns_uncompressed_content_unchanged(monkeypatch):
    # A ".gz"-suffixed key match whose bytes don't actually start with the
    # gzip magic number should be returned as-is rather than raising in
    # gzip.decompress -- guards the magic-byte check itself, not just the
    # decompression path.
    fetcher = _fetcher()

    def fake_get(url, timeout=60, stream=True):
        if url.endswith("_V06"):
            return SimpleNamespace(status_code=200, content=b"AR2V0006plainbytes")
        return SimpleNamespace(status_code=404, content=b"")

    monkeypatch.setattr(raf.requests, "get", fake_get)
    assert fetcher._download_scan(_SCAN_TIME) == b"AR2V0006plainbytes"


def _split_cut_volume():
    import numpy as np
    def sweep(moment, value):
        return [SimpleNamespace(
            header=SimpleNamespace(el_angle=0.5, az_angle=az),
            vol_consts=SimpleNamespace(lat=35.3, lon=-97.3),
            moments={moment: (SimpleNamespace(first_gate=0.25, gate_width=0.25), np.array([value, value]))},
        ) for az in (0, 90, 180, 270)]
    return SimpleNamespace(sweeps=[sweep(b'REF', 30), sweep(b'VEL', 10), sweep(b'SW', 5)])


def test_velocity_uses_compatible_split_cut_without_hiding_products(monkeypatch):
    import numpy as np
    fetcher = _fetcher()
    monkeypatch.setattr(fetcher, '_get_parsed', lambda *args: _split_cut_volume())
    scan = fetcher._decode(_SCAN_TIME, b'', product='velocity', tilt_idx=0)
    assert scan.pyart_field == 'velocity'
    assert scan.tilt_index == 1
    assert {'reflectivity', 'velocity', 'spectrum_width'} <= set(scan.available_products)
    np.testing.assert_allclose(scan.data, 19.4384)
    width = fetcher._decode(_SCAN_TIME, b'', product='spectrum_width', tilt_idx=0)
    np.testing.assert_allclose(width.data, 9.7192)
    fetcher.shutdown()


def test_product_changes_during_parse_do_not_change_requested_result(monkeypatch):
    fetcher = _fetcher()
    def parse(*args):
        fetcher._product = 'reflectivity'
        fetcher._tilt_idx = 2
        return _split_cut_volume()
    monkeypatch.setattr(fetcher, '_get_parsed', parse)
    fetcher._decode_and_cache(_SCAN_TIME, b'', (_SCAN_TIME, 'velocity', 0, None))
    assert fetcher._decoded_cache[(_SCAN_TIME, 'velocity', 0, None)].pyart_field == 'velocity'
    fetcher.shutdown()


def test_missing_product_does_not_silently_render_reflectivity(monkeypatch):
    import pytest
    fetcher = _fetcher()
    monkeypatch.setattr(fetcher, '_get_parsed', lambda *args: _split_cut_volume())
    with pytest.raises(RuntimeError, match='No differential_phase'):
        fetcher._decode(_SCAN_TIME, b'', product='differential_phase', tilt_idx=0)
    fetcher.shutdown()


def _volume_missing_first_radial():
    # Azimuths deliberately don't include anything near 0 deg -- e.g. a
    # dropped/missing radial near true north, a real WSR-88D data artifact.
    # After ArchiveRadarFetcher._decode sorts by azimuth, the smallest value
    # present (12.0) is not 0, so az_offset must be set to 12.0, not default
    # to 0.0, or _sample_scan_to_grid renders every ray rotated by 12 deg.
    import numpy as np
    def sweep(moment, value):
        return [SimpleNamespace(
            header=SimpleNamespace(el_angle=0.5, az_angle=az),
            vol_consts=SimpleNamespace(lat=35.3, lon=-97.3),
            moments={moment: (SimpleNamespace(first_gate=0.25, gate_width=0.25), np.array([value, value]))},
        ) for az in (12.0, 100.0, 200.0, 300.0)]
    return SimpleNamespace(sweeps=[sweep(b'REF', 30), sweep(b'VEL', 10), sweep(b'SW', 5)])


def test_decode_sets_az_offset_to_the_smallest_sorted_azimuth(monkeypatch):
    # Regression test: archive _decode() previously omitted az_offset
    # entirely from the Level2RadarScan(...) call, silently defaulting to
    # 0.0 and assuming ray 0 always sits at true north. Mirrors the live
    # decoder's az_offset=float(azimuths[0]) pattern (data/radar/radar_decoder.py).
    fetcher = _fetcher()
    monkeypatch.setattr(fetcher, '_get_parsed', lambda *args: _volume_missing_first_radial())
    scan = fetcher._decode(_SCAN_TIME, b'', product='reflectivity', tilt_idx=0)
    assert scan.az_offset == 12.0
    fetcher.shutdown()


def test_shutdown_keeps_files_until_running_task_finishes():
    import threading
    from pathlib import Path
    fetcher = _fetcher()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    path = Path(fetcher._tmpdir) / 'held.dat'
    path.write_bytes(b'data')
    emitted = []
    fetcher.error.connect(emitted.append)
    def work():
        started.set()
        release.wait(3)
        assert path.read_bytes() == b'data'
        fetcher._emit(fetcher.error, 'obsolete')
        finished.set()
    future = fetcher._fetch_executor.submit(work)
    assert started.wait(2)
    fetcher.shutdown()
    assert path.exists()
    release.set()
    future.result(timeout=3)
    assert finished.is_set()
    assert emitted == []


def test_velocity_options_are_part_of_the_velocity_cache_key_only():
    fetcher = _fetcher()
    raw_key = fetcher._decode_key(_SCAN_TIME)
    fetcher.set_velocity_options(raf.VelocityOptions(dealias=True))
    assert fetcher._decode_key(_SCAN_TIME) == raw_key            # reflectivity unaffected
    fetcher._product = "velocity"
    assert fetcher._decode_key(_SCAN_TIME)[3] == raf.VelocityOptions(dealias=True)
    fetcher.set_velocity_options(raf.VelocityOptions())
    assert fetcher._decode_key(_SCAN_TIME)[3] is None           # raw velocity key as before
    fetcher.shutdown()


def test_dealiasing_without_a_nyquist_says_raw_and_storm_relative_is_labelled(monkeypatch):
    import numpy as np
    fetcher = _fetcher()
    monkeypatch.setattr(fetcher, '_get_parsed', lambda *args: _split_cut_volume())
    scan = fetcher._decode(_SCAN_TIME, b'', product='velocity', tilt_idx=0,
                           velocity=raf.VelocityOptions(dealias=True, storm_motion=(0.0, 10.0)))
    assert scan.product == "SRV"
    assert scan.velocity_processing.startswith("raw: no Nyquist velocity recorded")
    assert "storm-relative to track motion from 180° at 10.0 m/s" in scan.velocity_processing
    # azimuth 0 beam: 10 m/s outbound minus the northward motion (x cos 0.5° tilt) -> ~0
    np.testing.assert_allclose(scan.data[0], 0.0, atol=1e-2)
    np.testing.assert_allclose(scan.data[2], 20.0 * 1.94384, rtol=1e-4)   # 180°, in knots
    fetcher.shutdown()


def test_a_failed_dealias_falls_back_to_raw_and_says_so(monkeypatch):
    import core.velocity_processing as vp
    fetcher = _fetcher()
    volume = _split_cut_volume()
    for radial in volume.sweeps[1]:
        radial.radial_consts = SimpleNamespace(nyq_vel=28.0)
    monkeypatch.setattr(fetcher, '_get_parsed', lambda *args: volume)
    monkeypatch.setattr(vp, 'dealias', lambda *a: (_ for _ in ()).throw(ValueError("too sparse")))
    scan = fetcher._decode(_SCAN_TIME, b'', product='velocity', tilt_idx=0,
                           velocity=raf.VelocityOptions(dealias=True))
    assert scan.product == "VEL" and scan.velocity_processing == "raw: dealiasing failed"
    fetcher.shutdown()


def test_a_seek_drops_stale_queued_downloads_and_fetches_the_new_scan_first(monkeypatch):
    import threading, time
    from datetime import timedelta
    fetcher = _fetcher()
    times = [_SCAN_TIME + timedelta(minutes=5 * i) for i in range(40)]
    fetcher._index = list(times)
    gate, started, lock = threading.Event(), [], threading.Lock()

    def slow_download(scan_time):
        with lock:
            started.append(scan_time)
        gate.wait(5)
        return None
    monkeypatch.setattr(fetcher, "_download_scan", slow_download)

    fetcher.on_time_changed(times[2])                 # queues scan 2 and its buffer
    deadline = time.time() + 2
    while len(started) < 2 and time.time() < deadline:
        time.sleep(0.01)
    busy = list(started)                              # both workers now blocked
    fetcher.on_time_changed(times[30] + timedelta(seconds=10))
    waiting = [key[1] for key, future in fetcher._queued.items() if not future.running()]
    assert waiting and all(t >= times[26] for t in waiting)   # nothing waiting for the old position
    gate.set()
    deadline = time.time() + 5
    while len(started) < len(busy) + 9 and time.time() < deadline:
        time.sleep(0.01)
    after = started[len(busy):]
    assert after[0] == times[30]
    assert after[1:5] == [times[31], times[29], times[32], times[28]]   # nearest first, ahead first
    assert not set(after) & set(times[:26])           # the old position's scans were dropped
    fetcher.shutdown()
