"""Video Studio: keyframed movies of an archive case (ui/studio/).

A movie is laid out in movie time (seconds of video), like an editor's
timeline. Each keyframe sits at a point of the movie (`at`) and fixes two
things there, as in Google Earth Studio: the case time (UTC) and the map
view (center, zoom, bearing, pitch). Between two keyframes the case clock
runs at a constant rate (the segment's speed: case seconds per video second,
derived from the spacing) and the camera moves with the keyframe's easing.
A keyframe can hold: stay still (clock and view) for `hold` seconds before
moving on.

Editing ripples like iMovie: lengthening a segment or a hold pushes every
later keyframe along, so the rest of the movie keeps its timing.

evaluate(m) gives the case time and view at movie time m; plan() one per
output frame. Pure logic, no Qt (tests/test_studio.py).
"""
from __future__ import annotations

import bisect
import json
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

EASINGS = ("smooth", "linear", "ease-in", "ease-out")
RESOLUTIONS = {                  # label -> (width, height); None = the map's size on screen
    "Map size": None,
    "720p (1280×720)": (1280, 720),
    "1080p (1920×1080)": (1920, 1080),
    "4K (3840×2160)": (3840, 2160),
}
# How often the case clock changes in the movie. "smooth" moves it every
# frame; the others hold it between steps, as a radar loop does, so only one
# frame per step has new data to draw (export reuses the rest unless the
# camera is moving) -- far quicker to render, and the data jumps a step at a time.
TIME_STEPS = {"smooth": None, "1 min": 60, "5 min": 300, "radar scans": "scans"}
DEFAULT_TIME_STEP = "1 min"
GIF_MAX_WIDTH = 960              # GIFs are 256-color and stored whole: kept small
DEFAULT_SPEED = 300.0            # case seconds per video second for a new move: about a radar scan a second
CAMERA_MOVE_S = 2.0              # a new keyframe at the same case time: a 2 s camera move
MIN_SEGMENT_S = 0.1


@dataclass
class View:
    lon: float
    lat: float
    zoom: float
    bearing: float = 0.0
    pitch: float = 0.0

    def describe(self) -> str:
        ns, ew = ("N" if self.lat >= 0 else "S"), ("E" if self.lon >= 0 else "W")
        return (f"{abs(self.lat):.3f}°{ns} {abs(self.lon):.3f}°{ew} · zoom {self.zoom:.1f}"
                + (f" · {self.bearing:.0f}°" if abs(self.bearing) > 0.5 and abs(self.bearing - 360) > 0.5 else "")
                + (f" · tilt {self.pitch:.0f}°" if self.pitch > 0.5 else ""))


@dataclass
class Keyframe:
    at: float                    # movie seconds where the keyframe starts
    time: datetime               # case time (UTC)
    view: View
    easing: str = "smooth"       # camera easing of the segment that starts here
    hold: float = 0.0            # movie seconds to stay here before that segment
    radar: dict | None = None    # {"station", "product", "tilt"}: the radar shown from here on (None: as is)

    @property
    def leaves(self) -> float:
        """Movie time the keyframe's segment starts (after its hold)."""
        return self.at + self.hold


@dataclass
class Overlays:
    time_stamp: bool = True
    status_line: bool = True
    legend: bool = False


@dataclass
class Project:
    keyframes: list[Keyframe] = field(default_factory=list)
    fps: int = 30
    resolution: str = "1080p (1920×1080)"
    format: str = "mp4"
    overlays: Overlays = field(default_factory=Overlays)
    name: str = "movie"
    time_step: str = DEFAULT_TIME_STEP

    # ---- reading -------------------------------------------------------
    def sorted(self) -> list[Keyframe]:
        return sorted(self.keyframes, key=lambda k: k.at)

    def duration(self) -> float:
        ks = self.sorted()
        return ks[-1].leaves if ks else 0.0

    def frame_count(self) -> int:
        return int(math.floor(self.duration() * self.fps + 1e-6)) + 1 if self.keyframes else 0

    def index(self, kf: Keyframe) -> int:
        return self.sorted().index(kf)

    def segment_length(self, i: int) -> float:
        """Movie seconds of the move from keyframe i to i+1 (its hold excluded)."""
        ks = self.sorted()
        return ks[i + 1].at - ks[i].leaves

    def segment_speed(self, i: int) -> float:
        """Case seconds per video second from keyframe i to i+1 (0: clock stopped)."""
        ks = self.sorted()
        length = self.segment_length(i)
        case_s = (ks[i + 1].time - ks[i].time).total_seconds()
        return case_s / length if length > 0 else 0.0

    def evaluate(self, m: float) -> tuple[datetime, View]:
        """The case time and view at movie time m."""
        ks = self.sorted()
        if not ks:
            raise ValueError("no keyframes")
        if m <= ks[0].leaves:
            return ks[0].time, ks[0].view
        for a, b in zip(ks, ks[1:]):
            if m < b.at:
                length = b.at - a.leaves
                u = 1.0 if length <= 0 else min(1.0, max(0.0, (m - a.leaves) / length))
                t = a.time + timedelta(seconds=(b.time - a.time).total_seconds() * u)
                return t, interpolate(a.view, b.view, ease(a.easing, u))
            if m <= b.leaves:
                return b.time, b.view
        return ks[-1].time, ks[-1].view

    def stepped(self, t: datetime, scan_times: list[datetime] = ()) -> datetime:
        """t held to the movie's time step: the minute (or 5) it's in, or the
        latest radar scan at or before it (the minute, before the first scan)."""
        step = TIME_STEPS.get(self.time_step)
        if step is None:
            return t
        if step == "scans":
            i = bisect.bisect_right(scan_times, t)
            if i:
                return scan_times[i - 1]
            step = 60                                    # before the first scan: whole minutes
        epoch = int(t.timestamp())
        return datetime.fromtimestamp(epoch - epoch % step, timezone.utc)

    def radar_at(self, m: float) -> dict | None:
        """The radar for movie time m: that of the latest keyframe reached
        (a movie switches radars at a keyframe)."""
        radar = None
        for k in self.sorted():
            if k.at > m + 1e-9:
                break
            radar = k.radar or radar
        return radar

    def retime(self, factor: float) -> None:
        """Stretch (factor > 1) or squeeze the whole movie: every keyframe's
        place and pause scale together, so moves keep their proportions."""
        factor = max(1e-3, factor)
        for k in self.keyframes:
            k.at *= factor
            k.hold *= factor


    def plan(self, scan_times: list[datetime] = ()) -> list[tuple[datetime, View]]:
        """One (case time, view) per output frame, at 0, 1/fps, 2/fps, ...,
        the time held to the time step."""
        if not self.keyframes:
            return []
        scans = sorted(scan_times)
        out = []
        for i in range(self.frame_count()):
            t, v = self.evaluate(i / self.fps)
            out.append((self.stepped(t, scans), v))
        return out

    def distinct_frames(self, scan_times: list[datetime] = ()) -> int:
        """How many frames differ from the one before: what an export has to draw."""
        n, last = 0, None
        for t, v in self.plan(scan_times):
            key = (t, round(v.lon, 7), round(v.lat, 7), round(v.zoom, 5), round(v.bearing, 4), round(v.pitch, 4))
            n += key != last
            last = key
        return n

    # ---- editing (ripple, like iMovie) ------------------------------------
    def _normalize(self) -> None:
        """Keep the keyframes in order with the first one at the movie's start."""
        self.keyframes = self.sorted()
        if self.keyframes and self.keyframes[0].at != 0.0:
            shift = self.keyframes[0].at
            for k in self.keyframes:
                k.at -= shift

    def keyframe_at(self, m: float, tolerance: float = 1e-3) -> Keyframe | None:
        return next((k for k in self.keyframes if abs(k.at - m) <= tolerance), None)

    def add(self, kf: Keyframe) -> Keyframe:
        """Insert kf at kf.at. Inside a segment it splits it and nothing else
        moves; where there's no room (on another keyframe, or in a hold) the
        later keyframes move along by a suggested gap."""
        before = [k for k in self.keyframes if k.at < kf.at - 1e-9]
        if before:                                   # not inside the previous keyframe's hold
            prev = max(before, key=lambda k: k.at)
            kf.at = max(kf.at, prev.leaves + MIN_SEGMENT_S)
        later = [k for k in self.keyframes if k.at >= kf.at - 1e-9]
        if later:
            nxt = min(later, key=lambda k: k.at)
            room = nxt.at - kf.at
            if room < kf.hold + MIN_SEGMENT_S:
                delta = kf.hold + self.suggested_gap(kf.time, nxt.time) - room
                for k in later:
                    k.at += delta
        self.keyframes.append(kf)
        self._normalize()
        return kf

    def append(self, time: datetime, view: View, easing: str = "smooth") -> Keyframe:
        """A keyframe after the last one, spaced by suggested_gap()."""
        ks = self.sorted()
        at = 0.0 if not ks else ks[-1].leaves + self.suggested_gap(ks[-1].time, time)
        kf = Keyframe(at=at, time=time, view=view, easing=easing)
        self.keyframes.append(kf)
        self._normalize()
        return kf

    @staticmethod
    def suggested_gap(a: datetime, b: datetime) -> float:
        """Movie seconds for a move from case time a to b: DEFAULT_SPEED, at
        least half a second; a camera-only move gets CAMERA_MOVE_S."""
        case_s = abs((b - a).total_seconds())
        return max(0.5, case_s / DEFAULT_SPEED) if case_s > 0 else CAMERA_MOVE_S

    def remove(self, kf: Keyframe, ripple: bool = False) -> None:
        """Delete kf. Ripple: the move across the gap gets a suggested length
        and later keyframes close up, rather than keeping their places."""
        ks = self.sorted()
        i = ks.index(kf)
        if ripple and 0 < i < len(ks) - 1:
            a, b = ks[i - 1], ks[i + 1]
            delta = (b.at - a.leaves) - self.suggested_gap(a.time, b.time)
            for k in ks[i + 1:]:
                k.at -= delta
        self.keyframes = [k for k in self.keyframes if k is not kf]
        self._normalize()

    def move(self, kf: Keyframe, at: float, ripple: bool = True) -> None:
        """Put kf at movie time `at`. Ripple: later keyframes move with it.
        Without: only kf moves, kept between its neighbors. The first
        keyframe stays at the start."""
        ks = self.sorted()
        i = ks.index(kf)
        if i == 0:
            return
        at = max(ks[i - 1].leaves + MIN_SEGMENT_S, at)
        if not ripple and i + 1 < len(ks):
            at = min(at, ks[i + 1].at - kf.hold - MIN_SEGMENT_S)
        delta = at - kf.at
        kf.at = at
        if ripple:
            for k in ks[i + 1:]:
                k.at += delta
        self._normalize()

    def set_hold(self, kf: Keyframe, hold: float) -> None:
        ks = self.sorted()
        hold = max(0.0, hold)
        delta = hold - kf.hold
        kf.hold = hold
        for k in ks[ks.index(kf) + 1:]:
            k.at += delta

    def set_segment_length(self, i: int, length: float) -> None:
        """Length (movie seconds) of the move from keyframe i to i+1; later keyframes ripple."""
        ks = self.sorted()
        delta = max(MIN_SEGMENT_S, length) - self.segment_length(i)
        for k in ks[i + 1:]:
            k.at += delta

    def set_segment_speed(self, i: int, speed: float) -> None:
        """Speed (case seconds per video second) of the move from keyframe i to i+1."""
        ks = self.sorted()
        case_s = abs((ks[i + 1].time - ks[i].time).total_seconds())
        if case_s > 0 and speed > 0:
            self.set_segment_length(i, case_s / speed)

    # ---- files ---------------------------------------------------------------
    def to_json(self) -> dict:
        def kf(k: Keyframe) -> dict:
            d = asdict(k)
            d["time"] = k.time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            return d
        return {"format_version": 2, "name": self.name, "fps": self.fps, "resolution": self.resolution,
                "format": self.format, "overlays": asdict(self.overlays), "time_step": self.time_step,
                "keyframes": [kf(k) for k in self.sorted()]}

    @classmethod
    def from_json(cls, d: dict) -> "Project":
        def when(s):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        raw = d.get("keyframes", [])
        if int(d.get("format_version", 2)) < 2:
            raw = _from_v1(raw)
        keyframes = [Keyframe(at=float(x["at"]), time=when(x["time"]), view=View(**x["view"]),
                              easing=x.get("easing", "smooth"), hold=float(x.get("hold", 0.0)),
                              radar=x.get("radar")) for x in raw]
        return cls(keyframes=sorted(keyframes, key=lambda k: k.at), fps=int(d.get("fps", 30)),
                   resolution=d.get("resolution", "1080p (1920×1080)"), format=d.get("format", "mp4"),
                   overlays=Overlays(**d.get("overlays", {})), name=d.get("name", "movie"),
                   time_step=d.get("time_step", "smooth") if d.get("time_step") in TIME_STEPS else "smooth")

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n")
        return path

    @classmethod
    def load(cls, path: Path) -> "Project":
        return cls.from_json(json.loads(Path(path).read_text()))

    def copy(self) -> "Project":
        return Project.from_json(self.to_json())


def _from_v1(raw: list[dict]) -> list[dict]:
    """Version-1 projects stored a speed or duration per segment; lay them out in movie time."""
    out, at = [], 0.0
    for i, x in enumerate(raw):
        y = dict(x)
        y["at"] = at
        out.append(y)
        if i + 1 < len(raw):
            a = datetime.fromisoformat(x["time"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(raw[i + 1]["time"].replace("Z", "+00:00"))
            case_s = (b - a).total_seconds()
            length = (x.get("duration") or (case_s / max(float(x.get("speed", 60.0)), 1e-6) if case_s > 0
                                              else CAMERA_MOVE_S))
            at += float(x.get("hold", 0.0)) + length
    return out


def ease(name: str, u: float) -> float:
    u = min(1.0, max(0.0, u))
    if name == "linear":
        return u
    if name == "ease-in":
        return u * u * u
    if name == "ease-out":
        return 1 - (1 - u) ** 3
    return u * u * (3 - 2 * u)                   # smooth: ease in and out


def interpolate(a: View, b: View, f: float) -> View:
    """The view a fraction f of the way from a to b: position along the
    Mercator line, zoom linearly (so scale changes geometrically), bearing
    the short way round, pitch linearly."""
    def merc_y(lat):
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    def merc_lat(y):
        return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2)
    if f <= 0:
        return replace(a)
    if f >= 1:
        return replace(b)
    lon = a.lon + (((b.lon - a.lon + 180) % 360) - 180) * f
    lat = merc_lat(merc_y(a.lat) + (merc_y(b.lat) - merc_y(a.lat)) * f)
    bearing = a.bearing + (((b.bearing - a.bearing + 180) % 360) - 180) * f
    return View(lon=lon, lat=lat, zoom=a.zoom + (b.zoom - a.zoom) * f,
                bearing=bearing % 360, pitch=a.pitch + (b.pitch - a.pitch) * f)
