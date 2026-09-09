"""Tests for runtime feature flags."""

import feature_flags
import runtime_flags as rf


def setup_function():
    rf.reset_flags()


def test_admin_only_features_disabled_by_default():
    assert feature_flags.is_enabled("mesoanalysis") is False
    assert feature_flags.is_enabled("noxp_radar") is False
    assert feature_flags.is_enabled("raw_lidar_quicklook") is False
    assert feature_flags.is_enabled("archive_asos") is False
    assert feature_flags.is_enabled("damage_paths") is False
    assert feature_flags.is_enabled("nlcd") is True
    assert feature_flags.is_enabled("satellite_basemap") is True
    assert feature_flags.is_enabled("sfcoa") is True


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
