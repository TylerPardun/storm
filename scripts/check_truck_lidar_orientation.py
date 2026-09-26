"""Are the LiDAR Truck's azimuths earth-referenced (true north), or relative
to the truck?

Physical test: fit the wind from the lidar's own conical PPI scans (a VAD
fit of radial velocity against azimuth) and compare its direction with an
independent wind at the same place and hour (HRRR 80 m, via Open-Meteo's
historical-forecast API). The truck parks facing a different way on each
deployment, so truck-relative azimuths would put the lidar's wind direction
off by an arbitrary 0-360 deg that changes from day to day; earth-referenced
azimuths agree with the independent wind to within normal model/height
differences. A constant offset instead would point at a fixed mounting or
sign error. The files' own Trailer_heading attribute is recorded alongside.

    python scripts/check_truck_lidar_orientation.py --out result.json
"""
import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIN_INTENSITY = 1.01          # SNR + 1; ~ -20 dB, a common Halo lidar threshold
HEIGHT_BAND_M = (150.0, 450.0)
MIN_RAYS = 8
MIN_AZ_COVERAGE = 270.0
MIN_R2 = 0.8
MIN_SPEED = 4.0               # m/s: below this a wind direction means little


def vad_fit(azimuth_deg, elevation_deg, radial, positive_away=True):
    """Wind (u, v, r2) from radial velocities. The CLAMPS/truck files say
    'positive values are towards the lidar', but rain in their vertical stares
    is negative (falling toward the lidar), so they are positive AWAY; see
    --assume-toward to test the documented convention instead."""
    radial_toward = -radial if positive_away else radial
    az = np.deg2rad(azimuth_deg)
    design = np.column_stack([np.ones_like(az), np.sin(az), np.cos(az)])
    coef, *_ = np.linalg.lstsq(design, radial_toward, rcond=None)
    fitted = design @ coef
    ss_res = np.sum((radial_toward - fitted) ** 2)
    ss_tot = np.sum((radial_toward - radial_toward.mean()) ** 2)
    ce = np.cos(np.deg2rad(elevation_deg))
    # toward-positive: v_r = -(u sin az + v cos az) cos el - w sin el
    return -coef[1] / ce, -coef[2] / ce, 1 - ss_res / ss_tot if ss_tot > 0 else 0.0


def direction_from(u, v):
    return float((270.0 - np.degrees(np.arctan2(v, u))) % 360.0)


def hrrr_80m(lat, lon, when, cache):
    key = (round(lat, 2), round(lon, 2), when.strftime("%Y-%m-%d"))
    if key not in cache:
        url = ("https://historical-forecast-api.open-meteo.com/v1/forecast"
               f"?latitude={lat:.4f}&longitude={lon:.4f}&start_date={key[2]}&end_date={key[2]}"
               "&hourly=wind_speed_80m,wind_direction_80m&models=ncep_hrrr_conus"
               "&wind_speed_unit=ms&timezone=UTC")
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={"User-Agent": "STORM/1.0"}), timeout=60) as r:
                    cache[key] = json.loads(r.read())["hourly"]
                break
            except Exception:
                time.sleep(2 + 3 * attempt)
        else:
            cache[key] = None
    hourly = cache[key]
    if not hourly:
        return None
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]
    i = int(np.argmin([abs((t - when).total_seconds()) for t in times]))
    speed, direction = hourly["wind_speed_80m"][i], hourly["wind_direction_80m"][i]
    return None if speed is None or direction is None else (float(speed), float(direction))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stream", default="DL1", help="DL1 or DL2 (the truck), or CLAMPS1 / CLAMPS2")
    parser.add_argument("--max-days", type=int, default=0, help="0 = every available day")
    parser.add_argument("--assume-toward", action="store_true",
                        help="treat velocity as positive toward the lidar (the files' comment; refuted by rain)")
    parser.add_argument("--scans-per-day", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("truck_lidar_orientation.json"))
    args = parser.parse_args()

    import net_compat
    net_compat.prefer_ipv4()
    import xarray as xr
    from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, discover_raw_lidar, load_raw_lidar
    from archive.fetchers.clamps_surface_archive_fetcher import _list_catalog_filenames

    platform = args.stream if args.stream.startswith("CLAMPS") else f"DLTRUCK1-{args.stream}"
    source = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.platform_id == f"{platform}-PPI")
    names = _list_catalog_filenames(source.platform_dir, source.datastream)
    days = sorted({n.split(".")[2] for n in names})
    if args.max_days:
        days = [days[i] for i in np.linspace(0, len(days) - 1, args.max_days).round().astype(int)]
    cache_dir = Path.home() / ".cache" / "storm" / "raw_lidar"
    hrrr_cache, results = {}, []

    for day in days:
        when = datetime.strptime(day, "%Y%m%d").date()
        try:
            assets = discover_raw_lidar(source, when)
        except Exception as exc:
            print(f"{day}: discovery failed: {exc}", flush=True)
            continue
        for asset in assets:
            try:
                rays = load_raw_lidar(asset, cache_dir)
                # download_asset caches each file under the SHA-256 of its URL
                cached = cache_dir / (hashlib.sha256(asset.url.encode()).hexdigest() + Path(asset.filename).suffix)
                with xr.open_dataset(cached, decode_times=False) as ds:
                    heading_attr = ds.attrs.get("Trailer_heading")
            except Exception as exc:
                print(f"{day}: {asset.filename} unreadable: {exc}", flush=True)
                continue
            velocity = np.ma.filled(rays.fields["velocity"]["data"].astype(float), np.nan)
            intensity = np.ma.filled(rays.fields["intensity"]["data"].astype(float), np.nan) \
                if "intensity" in rays.fields else np.full(velocity.shape, np.inf)
            dist_km = rays.distance_m / 1000.0 if np.nanmax(rays.distance_m) > 100 else rays.distance_m
            # group rays into scans: scan number change or a gap > 60 s
            scan_id = np.concatenate([[0], np.cumsum((np.diff(rays.scan_number) != 0) |
                                                     (np.diff(rays.time_epoch) > 60))])
            scans = [np.flatnonzero(scan_id == k) for k in np.unique(scan_id)]
            usable = []
            for idx in scans:
                el = float(np.nanmedian(rays.elevation_deg[idx]))
                az = rays.azimuth_deg[idx] % 360
                if idx.size < MIN_RAYS or not (10 < el < 85):
                    continue
                if 360 - np.max(np.diff(np.sort(np.concatenate([az, az[:1] + 360])))) < MIN_AZ_COVERAGE:
                    continue
                height = dist_km * 1000 * np.sin(np.deg2rad(el))
                band = (height >= HEIGHT_BAND_M[0]) & (height <= HEIGHT_BAND_M[1])
                v = np.where(intensity[idx][:, band] >= MIN_INTENSITY, velocity[idx][:, band], np.nan)
                per_ray = np.nanmedian(v, axis=1)
                good = np.isfinite(per_ray)
                if good.sum() < MIN_RAYS:
                    continue
                u, vv, r2 = vad_fit(az[good], el, per_ray[good], positive_away=not args.assume_toward)
                speed = float(np.hypot(u, vv))
                lat = float(np.nanmedian(rays.latitude[idx])); lon = float(np.nanmedian(rays.longitude[idx]))
                if r2 < MIN_R2 or speed < MIN_SPEED or not (np.isfinite(lat) and np.isfinite(lon)):
                    continue
                usable.append((idx, el, u, vv, r2, speed, lat, lon))
            for idx, el, u, vv, r2, speed, lat, lon in usable[:: max(1, len(usable) // args.scans_per_day)][: args.scans_per_day]:
                t = datetime.fromtimestamp(float(np.nanmedian(rays.time_epoch[idx])), timezone.utc)
                ref = hrrr_80m(lat, lon, t, hrrr_cache)
                if ref is None or ref[0] < MIN_SPEED:
                    continue
                lidar_dir = direction_from(u, vv)
                diff = (lidar_dir - ref[1] + 180) % 360 - 180
                results.append({"day": day, "file": asset.filename, "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "lat": round(lat, 4), "lon": round(lon, 4), "elevation_deg": round(el, 1),
                                "trailer_heading_attr": None if heading_attr is None else float(heading_attr),
                                "lidar_dir": round(lidar_dir, 1), "lidar_speed": round(speed, 1), "r2": round(r2, 3),
                                "hrrr80_dir": round(ref[1], 1), "hrrr80_speed": round(ref[0], 1),
                                "difference_deg": round(diff, 1)})
                print(f"{day} {t:%H:%M}Z lidar {lidar_dir:5.1f}° {speed:4.1f} m/s  HRRR80 {ref[1]:5.1f}° {ref[0]:4.1f}  "
                      f"diff {diff:+6.1f}  (Trailer_heading attr {heading_attr})", flush=True)

    diffs = np.array([r["difference_deg"] for r in results])
    summary = {"stream": args.stream, "scans_compared": len(results),
               "days": len({r["day"] for r in results})}
    if diffs.size:
        summary.update(median_abs_diff_deg=float(np.median(np.abs(diffs))),
                       within_30_deg=float(np.mean(np.abs(diffs) <= 30)),
                       within_45_deg=float(np.mean(np.abs(diffs) <= 45)),
                       circular_mean_diff_deg=float(np.degrees(np.angle(np.mean(np.exp(1j * np.deg2rad(diffs)))))),
                       resultant_length=float(np.abs(np.mean(np.exp(1j * np.deg2rad(diffs))))))
    args.out.write_text(json.dumps({"summary": summary, "method": __doc__, "scans": results}, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
