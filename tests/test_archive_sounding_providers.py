from datetime import datetime, timedelta, timezone
import numpy as np
from archive.fetchers import sounding_archive_fetcher as archive
from archive.fetchers import clamps_sonde_archive_fetcher as sonde
from archive.fetchers import clamps_tropoe_archive_fetcher as tropoe
from data.fetchers import clamps_sounding_fetcher as live
from core.sounding import Sounding, SoundingSet


def _set(when, lat=35.):
    a = np.array([900., 800., 700., 600., 500.])
    snd = Sounding(lat, -97., when, 0, 'test', a, a, a, a, a, a,
                   location_source='FOFS GPS')
    return SoundingSet(lat, -97., 300., when, [snd], source='nssl')


def test_thredds_sonde_works_when_live_api_is_down(monkeypatch):
    t = datetime(2024, 4, 27, 22, tzinfo=timezone.utc)
    expected = _set(t)
    monkeypatch.setattr(sonde, 'fetch_clamps_sonde_soundings', lambda _: expected)
    def fail():
        raise AssertionError('API should not be consulted for a usable THREDDS launch')
    monkeypatch.setattr(live, '_api_sonde_entries', fail)
    fetcher = archive.ArchiveSoundingFetcher()
    values = []
    fetcher.sounding_ready.connect(values.append)
    fetcher._do_fetch_nssl(t)
    assert values == [expected]


def test_provider_failure_does_not_block_tropoe_and_future_profiles_do_not_leak(monkeypatch):
    t = datetime(2024, 4, 27, 22, tzinfo=timezone.utc)
    def fail(*args):
        raise TimeoutError('down')
    monkeypatch.setattr(sonde, 'fetch_clamps_sonde_soundings', fail)
    monkeypatch.setattr(live, '_api_sonde_entries', fail)
    future = _set(t + timedelta(hours=1), lat=36.)
    older = _set(t - timedelta(hours=1))
    future.soundings.insert(0, older.soundings[0])
    future.source = 'clamps_tropoe'
    monkeypatch.setattr(tropoe, 'fetch_clamps_tropoe_soundings', lambda _: future)
    fetcher = archive.ArchiveSoundingFetcher()
    values = []
    fetcher.sounding_ready.connect(values.append)
    fetcher._do_fetch_nssl(t)
    assert len(values) == 1 and values[0].soundings == older.soundings
    assert values[0].lat == 35. and values[0].source == 'clamps_tropoe'


def test_hrrr_retains_actual_hour_and_unknown_run_and_lead(monkeypatch):
    t = datetime(2024, 4, 27, 20, 41, tzinfo=timezone.utc)
    hourly = {'time': ['2024-04-27T20:00']}
    for level in archive.PRESSURE_LEVELS:
        for var, value in [('temperature', 20.), ('dew_point', 10.), ('wind_u_component', -3.),
                           ('wind_v_component', -4.), ('geopotential_height', 1000.)]:
            hourly[f'{var}_{level}hPa'] = [value]
    payload = {'hourly': hourly, 'elevation': 300., 'latitude': 35.21, 'longitude': -97.41}
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    monkeypatch.setattr(archive.requests, 'get', lambda *a, **k: Response())
    fetcher = archive.ArchiveSoundingFetcher()
    values = []
    fetcher.sounding_ready.connect(values.append)
    fetcher._do_fetch_model(35.2, -97.4, t)
    assert values[0].source == 'hrrr'
    snd = values[0].soundings[0]
    assert snd.valid_time == t.replace(minute=0)
    assert snd.provenance['model_run_time'] is None
    assert snd.provenance['forecast_lead_hours'] is None
    assert 'Analysis' not in snd.label
    assert np.allclose(snd.wind_speed, 5.)
