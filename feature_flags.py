from dataclasses import dataclass

import runtime_flags


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    default_enabled: bool = True
    admin_only: bool = False


FEATURES = {
    "mesoanalysis": Feature(
        key="mesoanalysis",
        label="Mesoanalysis overlays",
        default_enabled=True,
        admin_only=True,
    ),
    "sfcoa": Feature(
        key="sfcoa",
        label="SFCOA overlays",
        default_enabled=True,
        admin_only=False,
    ),
    "nlcd": Feature(
        key="nlcd",
        label="NLCD land cover",
        default_enabled=True,
        admin_only=False,
    ),
    "satellite_basemap": Feature(
        key="satellite_basemap",
        label="Satellite basemap",
        default_enabled=True,
        admin_only=False,
    ),
    "noxp_radar": Feature(
        key="noxp_radar",
        label="NOXP mobile radar (archive)",
        default_enabled=True,
        admin_only=False,
    ),
    "raw_lidar_quicklook": Feature(
        key="raw_lidar_quicklook",
        label="CLAMPS raw lidar quicklook (archive)",
        default_enabled=True,
        admin_only=False,
    ),
    "archive_asos": Feature(
        key="archive_asos",
        label="ASOS (archive)",
        default_enabled=True,
        admin_only=False,
    ),
    "damage_paths": Feature(
        key="damage_paths",
        label="Damage paths (archive)",
        default_enabled=True,
        admin_only=False,
    ),
    "storm_track": Feature(
        key="storm_track",
        label="Storm track (archive)",
        default_enabled=True,
        admin_only=False,
    ),
    "obs_trails": Feature(
        key="obs_trails",
        label="Observation trails and time-to-space (archive)",
        default_enabled=True,
        admin_only=False,
    ),
}


def is_enabled(key: str) -> bool:
    feature = FEATURES[key]
    if feature.admin_only and not runtime_flags.FLAGS.admin_mode:
        return False
    return feature.default_enabled
