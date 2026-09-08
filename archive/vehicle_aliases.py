"""Vehicle ID translation between STORM MQTT and FOFS THREDDS archives."""

from __future__ import annotations


# Platform directory names observed under the FOFS Mobile-Mesonet THREDDS
# catalog (data.nssl.noaa.gov/thredds/fileServer/FOFS/Mobile-Mesonet/data).
# These are the THREDDS-side names directly -- no alias translation needed
# for them, since thredds_vehicle_id() passes unmapped names through
# unchanged. Used as the fallback probe roster for archive dates that have
# no MQTT vehicle history to discover platforms from (older campaigns:
# TORUS, RiVorS, and some LIFT dates all confirmed to have none -- see
# case_data/evidence/ and planning/source-and-pilot-register.md). Not every
# platform has data on every day; ArchiveVehicleObsFetcher already drops
# 404s, so probing the full roster is safe.
KNOWN_FOFS_PLATFORMS = (
    "dltruck", "farfield", "hailcam", "mg1", "mg2", "mg3", "noxp_scout",
    "probe1", "probe2", "probe3", "probe4", "probe5", "probe7", "probe9",
    "windsonde1", "windsonde2",
)


_MQTT_TO_THREDDS = {
    "lid1": "dltruck",
    "p1": "probe1",
    "p2": "probe2",
    "p3": "probe3",
    "p4": "probe4",
    "p5": "probe5",
    "p7": "probe7",
}

_THREDDS_TO_MQTT = {
    thredds_id: mqtt_id
    for mqtt_id, thredds_id in _MQTT_TO_THREDDS.items()
}


def thredds_vehicle_id(mqtt_vehicle_id: str) -> str:
    """Return the FOFS directory name for a STORM MQTT vehicle ID."""
    normalized = mqtt_vehicle_id.strip().lower()
    return _MQTT_TO_THREDDS.get(normalized, normalized)


def mqtt_vehicle_id(thredds_vehicle_id: str) -> str:
    """Return the STORM MQTT ID for a FOFS directory name."""
    normalized = thredds_vehicle_id.strip().lower()
    return _THREDDS_TO_MQTT.get(normalized, normalized)

