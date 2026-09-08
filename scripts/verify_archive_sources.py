#!/usr/bin/env python3
"""Probe real NSSL/NOAA archive sources for candidate campaign dates.

Reuses STORM's actual archive-fetcher code (ArchiveVehicleObsFetcher's
processed-netCDF/raw-CSV fetch path, annotation JSONL fetch, CLAMPS sonde
index) instead of reimplementing HTTP logic, so what this reports is
exactly what the running app would see for the same request.

Checks, per campaign date:
  - FOFS mobile-mesonet observations for the full known platform roster
    (not just the 7 currently aliased in archive/vehicle_aliases.py),
    via the same processed-netCDF-first/raw-CSV-fallback path the app uses
  - STORM's own recorded MQTT archive (storm.<topic>.<date> JSONL) —
    this is NOT a general NSSL archive, it only has content for dates
    STORM itself was deployed and connected
  - The CLAMPS sonde index, filtered to the date (the index itself may
    only cover recent/live launches — this probe exists to confirm that
    one way or another, not to assume it)

Does not touch application state or write anything inside the git repo.
Output goes to case_data/evidence/<campaign>-<date>.json (sibling of the
storm/ checkout, outside git).

Usage:
    python scripts/verify_archive_sources.py [--out DIR] [--campaign CODES]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_STORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_STORM_ROOT))

import config  # noqa: E402
from archive.fetchers.vehicle_obs_archive_fetcher import (  # noqa: E402
    ArchiveVehicleObsFetcher,
)
from archive.fetchers.mqtt_reader import _fetch_text as _fetch_annotations_text  # noqa: E402
from archive.vehicle_aliases import KNOWN_FOFS_PLATFORMS as FOFS_PLATFORMS  # noqa: E402
from data.fetchers.clamps_sounding_fetcher import _api_sonde_entries  # noqa: E402

_REQUEST_PACING_S = 0.3

ANNOTATION_TOPICS = ("vehicles", "scan_sectors", "cones", "drawings", "annotations")


@dataclass(frozen=True)
class CampaignDate:
    campaign: str
    date: str  # YYYYMMDD
    note: str


CANDIDATES = [
    CampaignDate("LIFT2024", "20240427", "Burkburnett/Lone Wolf/Harrold/Electra, TX/OK"),
    CampaignDate("LIFT2025", "20250519", "Ringgold/Leon/St. Jo, TX"),
    CampaignDate("LIFT2026", "20260517", "St. Libory, NE"),
    CampaignDate("TORUS2019", "20190528", "Waldo, KS EF2, 43 min"),
    CampaignDate("TORUS2022", "20220524", "Morton, TX EF2, 15 min"),
    CampaignDate("TORUS2023", "20230615", "Higgins, TX EF1, 4 min"),
    CampaignDate("RiVorS2017", "20170613", "Bushnell & Harrisburg, NE EF1 x2, same night"),
]


def _probe_fofs_platform(platform: str, date_str: str) -> dict:
    """Probe via the real ArchiveVehicleObsFetcher._fetch_vehicle path
    (processed netCDF first, raw CSV fallback) so this script stays a
    faithful mirror of what the app actually does, not a separate copy
    of the fetch logic that can drift out of sync with it."""
    session_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    fetcher = ArchiveVehicleObsFetcher(session_date)
    try:
        obs = fetcher._fetch_vehicle(platform, None)
    except Exception as e:  # noqa: BLE001 - this is a diagnostic probe
        return {"status": f"unexpected:{e}"}

    return {
        "status": "ok",
        "rows": len(obs),
        "first_time": obs[0].timestamp.isoformat() if obs else None,
        "last_time": obs[-1].timestamp.isoformat() if obs else None,
    }


def _probe_annotation_topic(topic: str, date_str: str) -> dict:
    url = f"{config.NSSL_API_ROOT}/annotations/storm.{topic}.{date_str}"
    try:
        text = _fetch_annotations_text(url)
    except Exception as e:  # noqa: BLE001
        return {"status": f"error:{e}", "url": url}
    if not text:
        return {"status": "empty_or_404", "url": url}
    return {"status": "ok", "lines": len(text.splitlines()), "url": url}


def _clamps_entries_by_date() -> dict:
    """Fetch the CLAMPS sonde index once; the app's own function has no
    date filter, so we fetch it once and bucket entries by date here."""
    try:
        entries = _api_sonde_entries()
    except Exception as e:  # noqa: BLE001
        return {"status": f"error:{e}", "by_date": {}}
    by_date: dict[str, list[str]] = {}
    for entry in entries:
        key = entry.file_time.strftime("%Y%m%d")
        by_date.setdefault(key, []).append(entry.file_time.isoformat())
    return {"status": "ok", "total_index_entries": len(entries), "by_date": by_date}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out", default=None,
        help="Output directory for evidence JSON (default: case_data/evidence next to storm/)",
    )
    parser.add_argument(
        "--campaign", default=None,
        help="Comma-separated campaign codes to run (default: all), e.g. LIFT2024,RiVorS2017",
    )
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else _STORM_ROOT.parent / "case_data" / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching CLAMPS sonde index once (shared across all dates)...")
    clamps = _clamps_entries_by_date()
    print(f"  CLAMPS index: {clamps['status']}, "
          f"{clamps.get('total_index_entries', 0)} total entries, "
          f"{len(clamps.get('by_date', {}))} distinct dates present")

    # NOTE: deliberately sequential (no ThreadPoolExecutor). In this dev
    # environment, requests to these NSSL hosts complete in ~5-7s when run
    # synchronously in a foreground shell, but hang indefinitely (no
    # exception, no timeout firing) when run from a background/detached
    # shell or from worker threads. Run this script in the foreground.
    #
    # A ~150-request unpaced sweep of this script in one session was
    # followed by data.nssl.noaa.gov connection timeouts (not clean error
    # responses) for several minutes, consistent with the WAF in front of
    # it soft-throttling a bursty client rather than a real outage -- see
    # planning/source-and-pilot-register.md. _REQUEST_PACING_S plus the
    # fetcher's own retry/backoff (vehicle_obs_archive_fetcher.py) make
    # this script, and the app, less likely to trigger or get tripped up
    # by that.
    candidates = CANDIDATES
    if args.campaign:
        wanted = {c.strip().upper() for c in args.campaign.split(",")}
        candidates = [c for c in CANDIDATES if c.campaign.upper() in wanted]

    for cd in candidates:
        print(f"\n=== {cd.campaign} {cd.date} ({cd.note}) ===")

        fofs_results = {}
        for platform in FOFS_PLATFORMS:
            r = _probe_fofs_platform(platform, cd.date)
            fofs_results[platform] = r
            rows = r.get("rows")
            detail = f"rows={rows}" if rows is not None else r["status"]
            print(f"  fofs/{platform}: {r['status']} ({detail})")
            time.sleep(_REQUEST_PACING_S)

        annot_results = {}
        for topic in ANNOTATION_TOPICS:
            r = _probe_annotation_topic(topic, cd.date)
            annot_results[topic] = r
            print(f"  annotations/{topic}: {r['status']}")
            time.sleep(_REQUEST_PACING_S)

        clamps_on_date = clamps.get("by_date", {}).get(cd.date, [])
        print(f"  clamps: {len(clamps_on_date)} sonde entries on this date")

        record = {
            "campaign": cd.campaign,
            "date": cd.date,
            "note": cd.note,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "fofs": fofs_results,
            "annotations": annot_results,
            "clamps_entries_on_date": clamps_on_date,
        }

        out_path = out_dir / f"{cd.campaign}-{cd.date}.json"
        out_path.write_text(json.dumps(record, indent=2))
        print(f"  -> wrote {out_path}")


if __name__ == "__main__":
    main()
