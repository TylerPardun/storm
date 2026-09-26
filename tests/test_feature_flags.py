"""Tests for runtime feature flags."""

import feature_flags
import runtime_flags as rf


def setup_function():
    rf.reset_flags()


def test_admin_only_features_disabled_by_default():
    assert feature_flags.is_enabled("mesoanalysis") is False
    assert feature_flags.is_enabled("nlcd") is True
    assert feature_flags.is_enabled("satellite_basemap") is True
    assert feature_flags.is_enabled("sfcoa") is True


def test_archive_features_are_for_every_user():
    """Released to all users 2026-09-26 once tested (no admin mode needed)."""
    for key in ("noxp_radar", "raw_lidar_quicklook", "archive_asos", "damage_paths",
                "storm_track", "obs_trails"):
        assert feature_flags.is_enabled(key) is True, key


def test_all_features_enabled_in_admin_mode():
    rf.FLAGS.admin_mode = True
    assert feature_flags.is_enabled("mesoanalysis") is True
    assert feature_flags.is_enabled("noxp_radar") is True
    assert feature_flags.is_enabled("raw_lidar_quicklook") is True
    assert feature_flags.is_enabled("archive_asos") is True
    assert feature_flags.is_enabled("damage_paths") is True
    assert feature_flags.is_enabled("nlcd") is True
    assert feature_flags.is_enabled("satellite_basemap") is True
    assert feature_flags.is_enabled("sfcoa") is True
