from datetime import datetime, timedelta, timezone
from core.observation import Observation
from archive.fetchers.clamps_surface_playback import ClampsSurfacePlayback


def test_surface_playback_never_uses_future_or_stale_observations():
    t = datetime(2026, 6, 10, tzinfo=timezone.utc)
    first = Observation('CLAMPS1', 35, -97, t, temperature_c=20)
    later = Observation('CLAMPS1', 35, -97, t + timedelta(minutes=10), temperature_c=21)
    other = Observation('CLAMPS2', 36, -96, t, temperature_c=22)
    playback = ClampsSurfacePlayback()
    playback.install([later, first, other])
    assert playback.at('CLAMPS1', t - timedelta(seconds=1)) is None
    assert playback.at('CLAMPS1', t + timedelta(minutes=4)) is first
    assert playback.at('CLAMPS1', t + timedelta(minutes=6)) is None
    assert playback.history('CLAMPS1', t) == [first]
    assert playback.at('CLAMPS2', t) is other


def test_surface_loader_keeps_all_platforms_and_prefers_first_source(monkeypatch):
    from archive.fetchers import clamps_surface_archive_fetcher as module
    from types import SimpleNamespace
    from io import BytesIO
    t = datetime(2026, 6, 10, tzinfo=timezone.utc)
    sources = [SimpleNamespace(platform_id=k, kind=v, trust_wind_direction=True)
               for k, v in [('CLAMPS1', 'met'), ('CLAMPS1', 'mwr'), ('CLAMPS2', 'mwr')]]
    monkeypatch.setattr(module, 'KNOWN_CLAMPS_SURFACE_SOURCES', sources)
    requested = []
    def find(source, day):
        requested.append((source.platform_id, source.kind))
        return ['https://example.test/data']
    monkeypatch.setattr(module, '_find_files', find)
    monkeypatch.setattr(module, '_urlopen_with_retry', lambda *a, **kw: BytesIO(b'data'))
    monkeypatch.setattr(module, 'parse_clamps_surface_netcdf', lambda data, key, trusted: [Observation(key, 35, -97, t)])
    rows = module.fetch_clamps_surface_observations(t)
    assert {obs.vehicle_id for obs in rows} == {'CLAMPS1', 'CLAMPS2'}
    assert requested == [('CLAMPS1', 'met'), ('CLAMPS2', 'mwr')]
