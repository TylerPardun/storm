#!/usr/bin/env python3
"""Priority 5 reconnaissance: representative validation matrix.

Runs STORM's real archive-fetcher code (not a reimplementation of the HTTP
logic) against a fixed set of representative campaign dates, and reports
file identities, parsed record counts, real time bounds, and basic
location/QC sanity per source -- the bar set in
planning/archive-browse-backlog.md's Priority 5 row: "Demonstrate
catalog-to-file-to-normalized-data behavior, not just HTTP success or
filename dates."

Sources probed (each via its real fetch function, called directly rather
than through the QObject/threaded wrapper some of them ship in, since the
probe only needs the synchronous result):
  - FOFS mobile mesonet   -- ArchiveVehicleObsFetcher._fetch_vehicle, all
                             16 known platform names (archive/vehicle_aliases.py)
  - Recorded sector/vehicle history -- mqtt_reader._fetch_topic_text
                             (THREDDS-first, API-fallback -- see mqtt_reader.py)
  - CLAMPS mobile sonde   -- fetch_clamps_sonde_soundings
  - CLAMPS wind (VAD)     -- _fetch_platform_wind_set, all 6 known sources
  - CLAMPS surface        -- fetch_clamps_surface_observations
  - CLAMPS TROPoe         -- fetch_clamps_tropoe_soundings
  - PERiLS CopterSonde    -- fetch_coptersonde_soundings (PERiLS dates only)

NOXP and raw scanning-lidar have separate bounded inventory/sample commands:
verify_noxp_archive.py and verify_raw_lidar_archive.py. They are not downloaded
implicitly by this multi-source matrix. Schema 2 records actual request URLs,
bytes and content hashes, and separates retrieval uncertainty from zero rows.

Does not touch application state or write anything inside the git repo.
Output goes to case_data/evidence/<campaign>-<date>.json (sibling of the
storm/ checkout, outside git).

Usage:
    python scripts/verify_archive_sources.py [--out DIR] [--campaign CODES] [--full-fofs-roster]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_STORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_STORM_ROOT))

from archive.fetchers.vehicle_obs_archive_fetcher import ArchiveVehicleObsFetcher  # noqa: E402
from archive.fetchers.mqtt_reader import _fetch_topic_text, _parse_jsonl, _TOPICS  # noqa: E402
from archive.fetchers.clamps_sonde_archive_fetcher import fetch_clamps_sonde_soundings  # noqa: E402
from archive.fetchers.clamps_wind_archive_fetcher import (  # noqa: E402
    KNOWN_CLAMPS_WIND_SOURCES, _fetch_platform_wind_set,
)
from archive.fetchers.clamps_surface_archive_fetcher import fetch_clamps_surface_observations  # noqa: E402
from archive.fetchers.clamps_tropoe_archive_fetcher import fetch_clamps_tropoe_soundings  # noqa: E402
from archive.fetchers.coptersonde_archive_fetcher import fetch_coptersonde_soundings  # noqa: E402
from archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS  # noqa: E402
from scripts.archive_probe_evidence import RequestEvidence

_REQUEST_PACING_S = 0.4

# NSSL's WAF has been observed to answer both data.nssl.noaa.gov and
# api.nssl.noaa.gov in ~5-7s per request even for a clean 404 (see
# planning/source-and-pilot-register.md, "Recorded sector history" --
# performance finding). A full 16-platform FOFS roster sweep is already
# ~16 requests; keep it to cases where the full-discovery sweep itself is
# the point, and use a short known-active roster elsewhere to stay within
# a reasonable total request budget across the whole matrix.
_SHORT_FOFS_ROSTER = ("dltruck", "probe1", "probe2", "probe3", "windsonde1")


@dataclass(frozen=True)
class CampaignDate:
    campaign: str
    date: str  # YYYYMMDD
    note: str
    fofs_roster: tuple = _SHORT_FOFS_ROSTER  # override to KNOWN_FOFS_PLATFORMS for full sweep
    check_coptersonde: bool = False
    also_check_next_day: bool = False  # for the cross-midnight case


CANDIDATES = [
    CampaignDate(
        "LIFT2024", "20240427",
        "Lone Wolf/Harrold/Electra/Burkburnett TX-OK -- CH2 multi-event date; "
        "outside THREDDS's ~5-month sector-history retention and the API is "
        "down, so this also exercises the both-sources-empty path for annotations",
        fofs_roster=KNOWN_FOFS_PLATFORMS,
    ),
    CampaignDate(
        "LIFT2025", "20250519",
        "Ringgold/Leon/St. Jo TX -- CH2 multi-event date",
    ),
    CampaignDate(
        "LIFT2026", "20260517",
        "St. Libory NE -- CH2 date, also NSSL-reported lidar collection; "
        "within THREDDS's sector-history retention window",
    ),
    CampaignDate(
        "CROSSMIDNIGHT", "20240506",
        "CH2 deployment period spanning both sides of midnight UTC -- check "
        "this date and the following day for operational-day boundary behavior",
        also_check_next_day=True,
    ),
    CampaignDate(
        "IAN2022", "20220928",
        "Hurricane Ian -- older (pre-LIFT), non-CH2 case. Already "
        "cross-verified in Priority 1: NOXP + FOFS dltruck/probe1 + CLAMPS "
        "DL Truck (wind+sonde) all confirmed active this window. NOXP has no "
        "in-app fetcher yet, so only the FOFS/CLAMPS side is probed live here.",
        fofs_roster=("dltruck", "probe1", "probe2", "probe3"),
    ),
    CampaignDate(
        "PERILS2022", "20220322",
        "PERiLS 2022 IOP1 -- CopterSonde ascent confirmed to exist for this "
        "date in Priority-3-era reconnaissance. Also checked against the "
        "FOFS/CLAMPS sources as a deliberate missing-source scenario, since "
        "PERiLS predates LIFT's FOFS/CLAMPS mobile deployment.",
        fofs_roster=("dltruck", "probe1", "probe2"),
        check_coptersonde=True,
    ),
    CampaignDate(
        "NODEPLOYMENT", "20200115",
        "No known STORM/NSSL mobile deployment -- deliberate all-sources-empty "
        "control case, to confirm the app's fetchers return cleanly empty "
        "rather than erroring when literally nothing is there.",
        fofs_roster=("dltruck", "probe1"),
    ),
]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _probe_fofs(date_str: str, roster: tuple) -> dict:
    session_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    fetcher = ArchiveVehicleObsFetcher(session_date)
    results = {}
    for platform in roster:
        t0 = time.time()
        try:
            obs = fetcher._fetch_vehicle(platform, None)
            results[platform] = {
                "status": "ok" if obs else "no_usable_records",
                "rows": len(obs),
                "invalid_coordinate_rows": sum(not (math.isfinite(o.lat) and math.isfinite(o.lon) and abs(o.lat) <= 90 and abs(o.lon) <= 180) for o in obs),
                "first_time": _iso(min(o.timestamp for o in obs)) if obs else None,
                "last_time": _iso(max(o.timestamp for o in obs)) if obs else None,
                "sample_lat": obs[0].lat if obs else None,
                "sample_lon": obs[0].lon if obs else None,
                "elapsed_s": round(time.time() - t0, 2),
            }
        except Exception as e:  # noqa: BLE001 - diagnostic probe
            results[platform] = {"status": f"error:{e}", "elapsed_s": round(time.time() - t0, 2)}
        print(f"    fofs/{platform}: {results[platform]['status']} "
              f"({results[platform].get('rows', '-')} rows, {results[platform]['elapsed_s']}s)")
        time.sleep(_REQUEST_PACING_S)
    return results


def _probe_annotations(date_str: str) -> dict:
    results = {}
    for topic in _TOPICS:
        t0 = time.time()
        try:
            text, source = _fetch_topic_text(topic, date_str)
            if text:
                records = _parse_jsonl(text)
                results[topic] = {
                    "status": "ok" if records else "no_usable_records", "source": source, "records": len(records),
                    "first_time": _iso(records[0][0]) if records else None,
                    "last_time": _iso(records[-1][0]) if records else None,
                    "elapsed_s": round(time.time() - t0, 2),
                }
            else:
                results[topic] = {"status": "no_usable_records", "source": source, "elapsed_s": round(time.time() - t0, 2)}
        except Exception as e:  # noqa: BLE001
            results[topic] = {"status": f"error:{e}", "elapsed_s": round(time.time() - t0, 2)}
        print(f"    annotations/{topic}: {results[topic]['status']} "
              f"(source={results[topic].get('source', '-')}, {results[topic]['elapsed_s']}s)")
        time.sleep(_REQUEST_PACING_S)
    return results


def _probe_clamps_sonde(archive_date: datetime) -> dict:
    t0 = time.time()
    try:
        sset = fetch_clamps_sonde_soundings(archive_date)
    except Exception as e:  # noqa: BLE001
        return {"status": f"error:{e}", "elapsed_s": round(time.time() - t0, 2)}
    elapsed = round(time.time() - t0, 2)
    if sset is None or not sset.soundings:
        return {"status": "no_usable_records", "elapsed_s": elapsed}
    return {
        "status": "ok", "elapsed_s": elapsed,
        "soundings": len(sset.soundings),
        "lat": sset.lat, "lon": sset.lon,
        "first_valid_time": _iso(sset.soundings[0].valid_time),
        "last_valid_time": _iso(sset.soundings[-1].valid_time),
        "launches": [{"valid_time": _iso(s.valid_time), "lat": s.lat, "lon": s.lon,
                      "location_source": s.location_source, "position_time": _iso(s.location_time),
                      "levels": int(s.pressure.size)} for s in sset.soundings],
    }


def _probe_clamps_wind(date_str: str) -> dict:
    results = {}
    for source in KNOWN_CLAMPS_WIND_SOURCES:
        t0 = time.time()
        try:
            vad_set = _fetch_platform_wind_set(source, date_str)
        except Exception as e:  # noqa: BLE001
            results[source.platform_id] = {"status": f"error:{e}", "elapsed_s": round(time.time() - t0, 2)}
            time.sleep(_REQUEST_PACING_S)
            continue
        elapsed = round(time.time() - t0, 2)
        if vad_set is None or len(vad_set) == 0:
            results[source.platform_id] = {"status": "no_usable_records", "elapsed_s": elapsed}
        else:
            results[source.platform_id] = {
                "status": "ok", "elapsed_s": elapsed,
                "profiles": len(vad_set),
                "first_time": _iso(vad_set[0].timestamp),
                "last_time": _iso(vad_set[-1].timestamp),
            }
        print(f"    clamps_wind/{source.platform_id}: {results[source.platform_id]['status']}")
        time.sleep(_REQUEST_PACING_S)
    return results


def _probe_clamps_surface(archive_date: datetime) -> dict:
    t0 = time.time()
    try:
        obs = fetch_clamps_surface_observations(archive_date)
    except Exception as e:  # noqa: BLE001
        return {"status": f"error:{e}", "elapsed_s": round(time.time() - t0, 2)}
    elapsed = round(time.time() - t0, 2)
    if not obs:
        return {"status": "no_usable_records", "elapsed_s": elapsed}
    return {
        "status": "ok", "elapsed_s": elapsed, "rows": len(obs),
        "first_time": _iso(min(o.timestamp for o in obs)), "last_time": _iso(max(o.timestamp for o in obs)),
        "sample_lat": obs[0].lat, "sample_lon": obs[0].lon,
    }


def _probe_clamps_tropoe(archive_date: datetime) -> dict:
    t0 = time.time()
    try:
        sset = fetch_clamps_tropoe_soundings(archive_date)
    except Exception as e:  # noqa: BLE001
        return {"status": f"error:{e}", "elapsed_s": round(time.time() - t0, 2)}
    elapsed = round(time.time() - t0, 2)
    if sset is None or not sset.soundings:
        return {"status": "no_usable_records", "elapsed_s": elapsed}
    return {
        "status": "ok", "elapsed_s": elapsed, "soundings": len(sset.soundings),
        "first_valid_time": _iso(min(s.valid_time for s in sset.soundings)),
        "last_valid_time": _iso(max(s.valid_time for s in sset.soundings)),
        "lat": sset.lat, "lon": sset.lon,
    }


def _probe_coptersonde(archive_date: datetime) -> dict:
    t0 = time.time()
    try:
        by_site = fetch_coptersonde_soundings(archive_date)
    except Exception as e:  # noqa: BLE001
        return {"status": f"error:{e}", "elapsed_s": round(time.time() - t0, 2)}
    elapsed = round(time.time() - t0, 2)
    if not by_site:
        return {"status": "no_usable_records", "elapsed_s": elapsed}
    return {
        "status": "ok", "elapsed_s": elapsed,
        "sites": {
            site: {
                "soundings": len(sset.soundings),
                "lat": sset.lat, "lon": sset.lon,
            }
            for site, sset in by_site.items()
        },
    }


def run_case(cd: CampaignDate, out_dir: Path) -> dict:
    print(f"\n=== {cd.campaign} {cd.date} ({cd.note}) ===")
    archive_date = datetime.strptime(cd.date, "%Y%m%d").replace(tzinfo=timezone.utc)

    source_checks = {}
    def capture(name, function, *args):
        with RequestEvidence() as evidence:
            result = function(*args)
        source_checks[name] = evidence.report()
        return result

    print("  -- FOFS mobile mesonet --")
    fofs = capture("fofs", _probe_fofs, cd.date, cd.fofs_roster)

    print("  -- recorded sector/vehicle history (mqtt_reader) --")
    annotations = capture("annotations", _probe_annotations, cd.date)

    print("  -- CLAMPS mobile sonde --")
    sonde = capture("sonde", _probe_clamps_sonde, archive_date)
    print(f"    {sonde['status']}")
    time.sleep(_REQUEST_PACING_S)

    print("  -- CLAMPS wind (VAD) --")
    wind = capture("wind", _probe_clamps_wind, cd.date)

    print("  -- CLAMPS surface --")
    surface = capture("surface", _probe_clamps_surface, archive_date)
    print(f"    {surface['status']}")
    time.sleep(_REQUEST_PACING_S)

    print("  -- CLAMPS TROPoe --")
    tropoe = capture("tropoe", _probe_clamps_tropoe, archive_date)
    print(f"    {tropoe['status']}")
    time.sleep(_REQUEST_PACING_S)

    coptersonde = None
    if cd.check_coptersonde:
        print("  -- PERiLS CopterSonde --")
        coptersonde = capture("coptersonde", _probe_coptersonde, archive_date)
        print(f"    {coptersonde['status']}")

    record = {
        "schema_version": 2,
        "source_checks": source_checks,
        "campaign": cd.campaign,
        "date": cd.date,
        "note": cd.note,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "fofs": fofs,
        "annotations": annotations,
        "clamps_sonde": sonde,
        "clamps_wind": wind,
        "clamps_surface": surface,
        "clamps_tropoe": tropoe,
        "coptersonde": coptersonde,
    }

    out_path = out_dir / f"{cd.campaign}-{cd.date}.json"
    temporary = out_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2))
    temporary.replace(out_path)
    print(f"  -> wrote {out_path}")

    if cd.also_check_next_day:
        next_day = archive_date.replace(hour=12) + timedelta(days=1)
        next_cd = CampaignDate(
            cd.campaign + "_NEXTDAY", next_day.strftime("%Y%m%d"),
            f"{cd.note} (following day, UTC)",
            fofs_roster=cd.fofs_roster,
        )
        following = run_case(next_cd, out_dir)
        boundaries = {}
        for vehicle, previous in fofs.items():
            last, first = previous.get("last_time"), following["fofs"].get(vehicle, {}).get("first_time")
            boundaries[vehicle] = {
                "previous_last": last, "following_first": first,
                "boundary_gap_seconds": (datetime.fromisoformat(first) - datetime.fromisoformat(last)).total_seconds() if first and last else None,
                "scope": "File endpoints only; does not assert gap-free observations throughout the interval",
            }
        (out_dir / f"{cd.campaign}-boundary.json").write_text(json.dumps(boundaries, indent=2))
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=None, help="Output directory (default: case_data/evidence next to storm/)")
    parser.add_argument("--campaign", default=None, help="Comma-separated campaign codes to run (default: all)")
    parser.add_argument("--full-fofs-roster", action="store_true",
                         help="Use the full 16-platform FOFS roster for every case, not just the primary ones")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else _STORM_ROOT.parent / "case_data" / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = CANDIDATES
    if args.campaign:
        wanted = {c.strip().upper() for c in args.campaign.split(",")}
        unknown = wanted - {c.campaign.upper() for c in CANDIDATES}
        if unknown:
            parser.error("Unknown campaign codes: " + ", ".join(sorted(unknown)))
        candidates = [c for c in CANDIDATES if c.campaign.upper() in wanted]

    for cd in candidates:
        if args.full_fofs_roster:
            cd = CampaignDate(**{**cd.__dict__, "fofs_roster": KNOWN_FOFS_PLATFORMS})
        run_case(cd, out_dir)


if __name__ == "__main__":
    main()
