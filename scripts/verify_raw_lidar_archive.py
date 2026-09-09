#!/usr/bin/env python3
"""Verify raw lidar inventory; --load explicitly downloads this source/date's files."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive.fetchers.raw_lidar_archive_fetcher import (
    KNOWN_RAW_LIDAR_SOURCES, discover_raw_lidar, load_raw_lidar,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=[s.platform_id for s in KNOWN_RAW_LIDAR_SOURCES], required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--load', action='store_true')
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache/storm/raw_lidar')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    source = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.platform_id == args.source)
    report = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(), 'source': asdict(source)}
    success = True
    try:
        assets = discover_raw_lidar(source, args.date)
        report['assets'] = [asdict(a) for a in assets]
        report['samples'] = []
        if args.load:
            for asset in assets:
                try:
                    report['samples'].append(load_raw_lidar(asset, args.cache).summary())
                except Exception as exc:
                    report['samples'].append({'url': asset.url, 'error': str(exc)})
                    success = False
    except Exception as exc:
        report['error'] = str(exc)
        success = False
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_suffix(args.out.suffix + '.tmp')
    temp.write_text(json.dumps(report, indent=2))
    temp.replace(args.out)
    return 0 if success else 2


if __name__ == '__main__':
    raise SystemExit(main())
