#!/usr/bin/env python3
"""Run the NOXP adapter with bounded inventory and explicit sample downloads.

Examples:
  python scripts/verify_noxp_archive.py --date 2022-09-28 --budget 80 --out evidence.json
  python scripts/verify_noxp_archive.py --asset 'https://data.nssl.noaa.gov/thredds/catalog/RRDD/NOXP/.../catalog.html?dataset=NSSL/NOXP/...' --out sample.json

Inventory does not download volumes. --asset explicitly loads that advertised
scientific file and records its checksum, size, parsed times, fields and masks.
Exit 2 means incomplete discovery or a failed sample, not an empty archive.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive.fetchers.noxp_archive_fetcher import NoxpArchive, ROOT, asset_from_url


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat)
    parser.add_argument('--catalog-root', help='Explicit NOXP subcatalog scope; recorded in the report')
    parser.add_argument('--budget', type=int, default=80)
    parser.add_argument('--passes', type=int, default=1, help='Reuse cached metadata across bounded passes')
    parser.add_argument('--asset', action='append', default=[], help='Explicit NOXP dataset-detail URL')
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache/storm/noxp')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.budget < 1 or args.passes < 1:
        parser.error('budget and passes must be positive')
    for url in args.asset:
        if not url.startswith(ROOT) or asset_from_url(url) is None:
            parser.error('asset must be a supported NOXP catalog dataset URL')
    adapter = NoxpArchive(args.cache)
    report = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat()}
    complete = True
    if not args.asset:
        for _ in range(args.passes):
            result = adapter.discover(args.date, budget=args.budget, catalog_root=args.catalog_root)
            if result.complete:
                break
        complete = result.complete
        report['inventory'] = asdict(result)
        report['complete'] = complete
    else:
        report['samples'] = []
        for url in args.asset:
            try:
                report['samples'].append(adapter.load(asset_from_url(url)).summary())
            except Exception as exc:
                complete = False
                report['samples'].append({'catalog_url': url, 'error': str(exc)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_suffix(args.out.suffix + '.tmp')
    temp.write_text(json.dumps(report, indent=2, default=str))
    temp.replace(args.out)
    return 0 if complete else 2


if __name__ == '__main__':
    raise SystemExit(main())
