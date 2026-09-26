"""Are the CLAMPS AERI-summary "outside air" sensors usable as surface met?

Compares outsideAirTemp / atmosphericRelativeHumidity / atmosphericPressure
in clampsaerisummaryC{1,2}.b1 with a trusted instrument on the same trailer
at the same time: the CLAMPS2 met tower (clampsmetC2.a1) and the CLAMPS1/2
MWR surface met (clampsmwrC{1,2}.a1), parsed exactly as STORM parses them.
Readings are matched to the minute. Per day: mean bias and RMSE for
temperature (degC), RH (%) and pressure (hPa), plus the share of AERI values
outside physical limits.

    python scripts/check_aeri_surface_met.py --days 12 --out result.json
"""
import argparse
import io
import json
import sys
from pathlib import Path
from urllib.request import Request

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAIRS = (  # (trailer dir, AERI stream, reference stream, reference kind)
    ("clamps/clamps2", "clampsaerisummaryC2.b1", "clampsmetC2.a1", "met tower"),
    ("clamps/clamps2", "clampsaerisummaryC2.b1", "clampsmwrC2.a1", "MWR"),
    ("clamps/clamps1", "clampsaerisummaryC1.b1", "clampsmwrC1.a1", "MWR"),
)


def _download(c, url):
    with c._urlopen_with_retry(Request(url, headers={"User-Agent": c._USER_AGENT}), timeout=180) as r:
        return r.read()


def _minute_series(times_epoch, values):
    minutes = (np.asarray(times_epoch) // 60).astype(np.int64)
    out = {}
    for m in np.unique(minutes):
        v = np.asarray(values)[minutes == m]
        v = v[np.isfinite(v)]
        if v.size:
            out[int(m)] = float(np.median(v))
    return out


def _rh(t_c, td_c):
    return 100 * np.exp(17.625 * td_c / (243.04 + td_c)) / np.exp(17.625 * t_c / (243.04 + t_c))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=10, help="overlap days sampled per pair")
    parser.add_argument("--out", type=Path, default=Path("aeri_surface_met.json"))
    args = parser.parse_args()
    import net_compat
    net_compat.prefer_ipv4()
    import xarray as xr
    from archive.fetchers import clamps_surface_archive_fetcher as c

    report = []
    units_mislabelled = set()
    for trailer, aeri_stream, ref_stream, ref_kind in PAIRS:
        aeri_files = c._list_catalog_filenames(trailer, aeri_stream)
        ref_files = c._list_catalog_filenames(trailer, ref_stream)
        aeri_by_day, ref_by_day = {}, {}
        for n in aeri_files:
            aeri_by_day.setdefault(n.split(".")[2], []).append(n)
        for n in ref_files:
            ref_by_day.setdefault(n.split(".")[2], []).append(n)
        overlap = sorted(set(aeri_by_day) & set(ref_by_day))
        print(f"== {aeri_stream} vs {ref_kind} ({ref_stream}): {len(overlap)} overlapping days", flush=True)
        if not overlap:
            continue
        picks = [overlap[i] for i in np.linspace(0, len(overlap) - 1, min(args.days, len(overlap))).round().astype(int)]
        for day in sorted(set(picks)):
            try:
                a_t, a_T, a_RH, a_P = [], [], [], []
                for name in aeri_by_day[day]:
                    data = _download(c, f"{c._FRDD_ROOT}/{trailer}/ingested/{aeri_stream}/{name}")
                    ds = xr.open_dataset(io.BytesIO(data), decode_times=False)
                    if "base_time" in ds and "time_offset" in ds:     # older files: time is a row index
                        epoch = float(ds["base_time"].values) + ds["time_offset"].values.astype(float)
                    else:
                        epoch = xr.decode_cf(ds[["time"]])["time"].values.astype("datetime64[s]").astype(np.int64)
                    a_t.append(np.asarray(epoch, dtype=float))
                    temp = ds["outsideAirTemp"].values.astype(float)
                    if np.nanmedian(temp) < 150:                       # labelled Kelvin but holds degC
                        units_mislabelled.add(day)
                        temp = temp + 273.15
                    a_T.append(temp - 273.15)
                    a_RH.append(ds["atmosphericRelativeHumidity"].values.astype(float))
                    a_P.append(ds["atmosphericPressure"].values.astype(float))
                a_t, a_T, a_RH, a_P = (np.concatenate(x) for x in (a_t, a_T, a_RH, a_P))
                r_t, r_T, r_Td, r_P = [], [], [], []
                for name in ref_by_day[day]:
                    data = _download(c, f"{c._FRDD_ROOT}/{trailer}/ingested/{ref_stream}/{name}")
                    for o in c.parse_clamps_surface_netcdf(data, trailer, True):
                        r_t.append(o.timestamp.timestamp())
                        r_T.append(np.nan if o.temperature_c is None else o.temperature_c)
                        r_Td.append(np.nan if o.dewpoint_c is None else o.dewpoint_c)
                        r_P.append(np.nan if o.pressure_mb is None else o.pressure_mb)
                r_t, r_T, r_Td, r_P = (np.array(x, dtype=float) for x in (r_t, r_T, r_Td, r_P))
            except Exception as exc:
                print(f"  {day}: unreadable ({exc})", flush=True)
                continue
            row = {"pair": f"{aeri_stream} vs {ref_kind}", "day": day, "aeri_samples": int(a_t.size),
                   "reference_samples": int(r_t.size),
                   "aeri_temperature_labelled_K_but_degC": day in units_mislabelled,
                   "aeri_impossible_share": {
                       "T": float(np.mean((a_T < -50) | (a_T > 55))),
                       "RH": float(np.mean((a_RH < 1) | (a_RH > 105))),
                       "p": float(np.mean((a_P < 600) | (a_P > 1085))),
                   }}
            for key, a_vals, r_vals in (("T", a_T, r_T), ("RH", a_RH, _rh(r_T, r_Td)), ("p", a_P, r_P)):
                am, rm = _minute_series(a_t, a_vals), _minute_series(r_t, r_vals)
                common = sorted(set(am) & set(rm))
                if len(common) < 30:
                    row[key] = None
                    continue
                d = np.array([am[m] - rm[m] for m in common])
                row[key] = {"minutes": len(common), "bias": round(float(np.mean(d)), 2),
                            "rmse": round(float(np.sqrt(np.mean(d ** 2))), 2),
                            "aeri_median": round(float(np.median([am[m] for m in common])), 2),
                            "ref_median": round(float(np.median([rm[m] for m in common])), 2)}
            report.append(row)
            fmt = lambda k: ("—" if row[k] is None else f"bias {row[k]['bias']:+7.2f} rmse {row[k]['rmse']:6.2f}")
            print(f"  {day}  T {fmt('T')} | RH {fmt('RH')} | p {fmt('p')} | impossible "
                  f"{row['aeri_impossible_share']}", flush=True)
    args.out.write_text(json.dumps({"method": __doc__, "days": report}, indent=2))
    for pair in sorted({r["pair"] for r in report}):
        rows = [r for r in report if r["pair"] == pair]
        for key in ("T", "RH", "p"):
            vals = [r[key] for r in rows if r[key]]
            if vals:
                print(f"SUMMARY {pair} {key}: {len(vals)} days, median |bias| "
                      f"{np.median([abs(v['bias']) for v in vals]):.2f}, median rmse {np.median([v['rmse'] for v in vals]):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
