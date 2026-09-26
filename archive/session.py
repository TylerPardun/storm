
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class ArchiveSession:
    """
    Immutable configuration for an archive replay session.

    Attributes
    ----------
    start_time : datetime
        UTC datetime at which the replay begins.
    radar_station : str | None
        NEXRAD 4-letter station ID (e.g. "KTLX").  None until the first
        vehicle position is found and the nearest station is resolved.
    """

    start_time: datetime
    radar_station: Optional[str] = None

    def __post_init__(self):
        # normalise to UTC so callers don't have to worry about tz.
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=timezone.utc)
        else:
            self.start_time = self.start_time.astimezone(timezone.utc)

    @property
    def date_str(self) -> str:
        """UTC date string YYYY-MM-DD."""
        return self.start_time.strftime("%Y-%m-%d")

    @property
    def date_compact(self) -> str:
        """UTC date string YYYYMMDD."""
        return self.start_time.strftime("%Y%m%d")


# An archive session covers its UTC day and, since intercepts routinely run
# into the evening after 00Z, up to 06Z of the next day. Where inside that
# span the timeline actually ends depends on the day's activity -- the last
# time a vehicle was driving or the crew touched a scan sector, annotation,
# cone or drawing -- plus a margin; that can be well before 00Z on a quiet
# day. Mesonet racks are sometimes left logging overnight, so a vehicle
# that is only parked never extends the session. Activity before 12Z on the
# session day is the previous local day's evening (e.g. the drive back to
# the hotel after 00Z), so it says nothing about when this session ends;
# with no activity from 12Z on, the session keeps its full UTC day.
SESSION_CAP_PAST_MIDNIGHT = timedelta(hours=6)
ACTIVITY_MARGIN = timedelta(minutes=30)
SESSION_DAY_ACTIVITY_FROM = timedelta(hours=12)
# Driving: covering more than this distance within a minute.
_MOVING_KM_PER_MINUTE = 0.12  # 2 m/s
_MOVING_WINDOW_S = 60


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def session_bounds(session_time: datetime) -> tuple[datetime, datetime]:
    """(start of the session's UTC day, latest possible end)."""
    start = _utc(session_time).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1) + SESSION_CAP_PAST_MIDNIGHT


def last_moving_time(observations: Iterable) -> Optional[datetime]:
    """Latest time a vehicle track shows it driving (not merely logging while
    parked). `observations` need .timestamp/.lat/.lon, sorted by time."""
    observations = list(observations)
    latest = None
    j = 0
    for i, obs in enumerate(observations):
        while (observations[j].timestamp - obs.timestamp).total_seconds() < -_MOVING_WINDOW_S:
            j += 1
        if j < i and _km(observations[j], obs) > _MOVING_KM_PER_MINUTE:
            latest = obs.timestamp
    return latest


def activity_end(
    session_time: datetime,
    activity_times: Iterable[Optional[datetime]],
) -> Optional[datetime]:
    """Where the timeline should end, or None when nothing shows this day's
    activity (then the caller keeps the full UTC day, as before). Only
    activity from 12Z on the session day counts. Never earlier than half an
    hour after the time the session was opened at, never past the 06Z cap."""
    start, cap = session_bounds(session_time)
    times = [t for t in activity_times
             if t is not None and start + SESSION_DAY_ACTIVITY_FROM <= t <= cap]
    if not times:
        return None
    opened = _utc(session_time)
    return min(cap, max(max(times) + ACTIVITY_MARGIN, opened + ACTIVITY_MARGIN))


def _km(a, b) -> float:
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a.lat)) * math.cos(math.radians(b.lat)) * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))
