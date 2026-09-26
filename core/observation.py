
from datetime import datetime, timezone


# observation record
class Observation:
    # create a new observation instance
    def __init__(
        self,
        vehicle_id,
        lat,
        lon,
        timestamp,
        icon_type=None,
        temperature_c=None,
        dewpoint_c=None,
        wind_speed_ms=None,
        wind_dir_deg=None,
        pressure_mb=None,
        heading_deg=None,
    ):
        # assign vehicle id
        self.vehicle_id = vehicle_id
        # assign latitude
        self.lat = lat
        # assign longitude
        self.lon = lon
        # assign timestamp (utc)
        self.timestamp = timestamp
        # assign icon type when available
        self.icon_type = icon_type
        # assign temperature (c)
        self.temperature_c = temperature_c
        # assign dewpoint (c)
        self.dewpoint_c = dewpoint_c
        # assign wind speed (m/s)
        self.wind_speed_ms = wind_speed_ms
        # assign wind direction (deg, from)
        self.wind_dir_deg = wind_dir_deg
        # assign pressure (mb/hpa)
        self.pressure_mb = pressure_mb
        # vehicle compass heading (deg, direction it faces), when recorded
        self.heading_deg = heading_deg

    # factory: create an observation timestamped to now (utc)
    @classmethod
    def new(cls, vehicle_id, lat, lon, **kwargs):
        # build a new record
        return cls(
            vehicle_id=vehicle_id,
            lat=lat,
            lon=lon,
            timestamp=datetime.now(timezone.utc),
            **kwargs,
        )
