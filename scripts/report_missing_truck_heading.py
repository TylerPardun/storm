"""Report LiDAR Truck lidar files whose truck heading is missing, with the
heading STORM estimates from the truck compass, so the data can be fixed.

The truck's azimuths are relative to the truck, so each file needs the
truck heading (global attribute Trailer_heading) to be placed on a map; many
files record -999 / NaN / a 0.0 default instead. For each such file this
reports the truck's compass heading (FOFS mesonet compass_dir) over the
file's time span -- what STORM uses in its place (see
archive/fetchers/raw_lidar_archive_fetcher.py:_estimate_truck_heading).

Only file headers are read (HTTP range requests), so large files are not
downloaded. The fixed-point product points straight up, where heading does
not matter, and is skipped unless --products includes fp.

    python scripts/report_missing_truck_heading.py --out missing_truck_heading.csv
"""
import argparse
import csv
import io
import math
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file via HTTP Range requests."""

    BLOCK = 256 * 1024

    def __init__(self, url, opener):
        self.url, self._open, self._pos, self._blocks, self._size = url, opener, 0, {}, None

    def _get(self, start, end):
        request = Request(self.url, headers={"User-Agent": "Mozilla/5.0 STORM/1.0", "Range": f"bytes={start}-{end}"})
        with self._open(request, timeout=120) as response:
            if self._size is None:
                total = response.headers.get("Content-Range", "").rsplit("/", 1)[-1]
                self._size = int(total) if total.isdigit() else None
            return response.read()

    def _block(self, index):
        if index not in self._blocks:
            self._blocks[index] = self._get(index * self.BLOCK, (index + 1) * self.BLOCK - 1)
        return self._blocks[index]

    def size(self):
        if self._size is None:
            self._block(0)
        return self._size

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        self._pos = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size()}[whence] + offset
        return self._pos

    def readinto(self, buffer):
        n, out = len(buffer), bytearray()
        while len(out) < n and self._pos + len(out) < self.size():
            at = self._pos + len(out)
            block = self._block(at // self.BLOCK)
            chunk = block[at % self.BLOCK: at % self.BLOCK + n - len(out)]
            if not chunk:
                break
            out += chunk
        buffer[:len(out)] = out
        self._pos += len(out)
        return len(out)


def file_header(url, opener):
    import h5py
    with h5py.File(io.BufferedReader(RangeFile(url, opener), buffer_size=RangeFile.BLOCK), "r") as f:
        heading = f.attrs.get("Trailer_heading")
        heading = float(np.asarray(heading).ravel()[0]) if heading is not None else None
        base = float(np.asarray(f["base_time"][()]).ravel()[0])
        offsets = f["time_offset"]
        n = offsets.shape[0]
        first, last = (float(offsets[0]), float(offsets[n - 1])) if n else (math.nan, math.nan)
    return heading, base + first, base + last


def heading_status(value):
    if value is None or not math.isfinite(value):
        return "missing (NaN / absent)"
    if value <= -900:
        return "missing (-999)"
    if value == 0.0:
        return "missing (0.0 default)"
    return "recorded"


def compass_estimate(start, end, tracks, load_track):
    readings = []
    day = start.date()
    while day <= end.date():
        if day not in tracks:
            try:
                tracks[day] = load_track(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)) or []
            except Exception:
                tracks[day] = []
        readings += [o.heading_deg for o in tracks[day]
                     if getattr(o, "heading_deg", None) is not None and start <= o.timestamp <= end]
        day += timedelta(days=1)
    if not readings:
        return None, None, 0
    a = np.deg2rad(readings)
    s, c = np.sin(a).mean(), np.cos(a).mean()
    spread = float(np.rad2deg(np.sqrt(-2 * np.log(max(np.hypot(s, c), 1e-9)))))
    return round(float(np.rad2deg(np.arctan2(s, c)) % 360), 1), round(spread, 1), len(readings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--products", default="ppi,csm,other")
    parser.add_argument("--streams", default="DL1,DL2")
    parser.add_argument("--out", type=Path, default=Path("missing_truck_heading.csv"))
    args = parser.parse_args()
    import net_compat
    net_compat.prefer_ipv4()
    from archive.fetchers import clamps_surface_archive_fetcher as c
    from archive.fetchers.vehicle_obs_archive_fetcher import load_dltruck_track

    jobs = []
    for stream in args.streams.split(","):
        for product in args.products.split(","):
            datastream = f"dltruckdl{product}{stream}.b1"
            for name in c._list_catalog_filenames("dltruck/dltruck1", datastream):
                jobs.append((stream, product, name, f"{c._FRDD_ROOT}/dltruck/dltruck1/ingested/{datastream}/{name}"))
    print(f"{len(jobs)} files to check", flush=True)

    def header(job):
        try:
            return job, file_header(job[3], c._urlopen_with_retry), None
        except Exception as exc:
            return job, None, str(exc)

    with ThreadPoolExecutor(4) as pool:
        headers = list(pool.map(header, jobs))

    tracks, rows = {}, []
    for (stream, product, name, url), result, error in headers:
        row = {"stream": stream, "product": product, "file": name, "url": url}
        if error:
            row.update(status=f"unreadable: {error}")
            rows.append(row)
            continue
        heading, t0, t1 = result
        start = datetime.fromtimestamp(t0, timezone.utc)
        end = datetime.fromtimestamp(t1, timezone.utc)
        status = heading_status(heading)
        row.update(start_utc=f"{start:%Y-%m-%d %H:%M:%S}", end_utc=f"{end:%Y-%m-%d %H:%M:%S}",
                   trailer_heading_in_file=heading, status=status)
        if status != "recorded":
            estimate, spread, count = compass_estimate(start, end, tracks, load_dltruck_track)
            row.update(compass_heading_estimate=estimate, compass_spread_deg=spread, compass_readings=count,
                       note=("no truck compass data for this span: cannot be oriented" if estimate is None else
                             "truck moved / turned during the file: heading varies" if spread > 20 else ""))
        rows.append(row)

    columns = ["stream", "product", "file", "start_utc", "end_utc", "trailer_heading_in_file", "status",
               "compass_heading_estimate", "compass_spread_deg", "compass_readings", "note", "url"]
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {args.out}")
    for key, count in sorted(Counter((r.get("status", "?").split(":")[0]) for r in rows).items()):
        print(f"  {key:26s} {count}")
    by_year = Counter((r["start_utc"][:4], r["status"] != "recorded") for r in rows if r.get("start_utc"))
    for year in sorted({y for y, _ in by_year}):
        print(f"  {year}: {by_year[(year, True)]} missing / {by_year[(year, True)] + by_year[(year, False)]} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
