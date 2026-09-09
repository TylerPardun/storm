from datetime import datetime, timedelta, timezone

from core.observation import Observation
from ui.layers.surface_plot_layer import SurfacePlotLayer


def _obs(ts):
    return Observation("surface:asos:OUN", 35.24, -97.47, ts, temperature_c=20.0)


def test_default_reference_time_matches_live_mode_wall_clock_behavior():
    # No reference_time given -- an observation from right now should read
    # as fresh, exactly like before this parameter existed.
    color = SurfacePlotLayer._obs_age_color(_obs(datetime.now(timezone.utc)), "surface:asos:OUN")
    assert color == "#39D98A"  # green/fresh


def test_archive_reference_time_near_the_observation_reads_fresh():
    archive_now = datetime(2019, 5, 20, 21, 0, tzinfo=timezone.utc)
    obs = _obs(archive_now - timedelta(minutes=10))
    color = SurfacePlotLayer._obs_age_color(obs, "surface:asos:OUN", reference_time=archive_now)
    assert color == "#39D98A"


def test_archive_reference_time_years_after_the_observation_still_reads_fresh_relative_to_it():
    # This is the bug this parameter exists to fix: an observation from
    # 2019 is not "stale" if the archive clock is also sitting in 2019
    # a few minutes later -- only wall-clock now() would make it look that
    # old, which is exactly what reference_time avoids.
    obs = _obs(datetime(2019, 5, 20, 20, 50, tzinfo=timezone.utc))
    archive_now = datetime(2019, 5, 20, 21, 0, tzinfo=timezone.utc)
    color = SurfacePlotLayer._obs_age_color(obs, "surface:asos:OUN", reference_time=archive_now)
    assert color == "#39D98A"

    # Without passing reference_time (i.e. the old, real-now()-relative
    # behavior), the same 2019 observation is many years stale.
    stale_color = SurfacePlotLayer._obs_age_color(obs, "surface:asos:OUN")
    assert stale_color == "#E53935"  # red/stale


def test_non_asos_stations_use_the_tighter_five_minute_threshold():
    archive_now = datetime(2019, 5, 20, 21, 0, tzinfo=timezone.utc)
    obs = _obs(archive_now - timedelta(minutes=6))
    color = SurfacePlotLayer._obs_age_color(obs, "surface:ok:SOME_STID", reference_time=archive_now)
    assert color == "#FFD166"  # yellow -- would still be green under ASOS's 70-min threshold
