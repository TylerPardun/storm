"""Archive mode's features are for every archive user, not only admin mode
(which turns on just the live mesoanalysis overlays)."""
import feature_flags
import runtime_flags

ARCHIVE_FEATURES = ("noxp_radar", "raw_lidar_quicklook", "archive_asos", "damage_paths", "storm_track", "obs_trails")


def test_archive_features_are_on_without_admin_mode(monkeypatch):
    monkeypatch.setattr(runtime_flags.FLAGS, "admin_mode", False)
    for key in ARCHIVE_FEATURES:
        assert not feature_flags.FEATURES[key].admin_only, key
        assert feature_flags.is_enabled(key), key


def test_only_mesoanalysis_needs_admin_mode():
    assert [k for k, f in feature_flags.FEATURES.items() if f.admin_only] == ["mesoanalysis"]
