"""Check core.base_state against MESO-VIEW's RAP/RUC base states.

MESO-VIEW's builder recorded, for every analysis time, the storm center,
height Z and model hour it used (base_state_data_tsc5_{T,N}_debug.csv).
This recomputes those times with STORM from the same inputs and reports
the differences from MESO-VIEW's base_state_data_tsc5.pkl. Moisture fields
differ by MetPy's 1.7 saturation-vapor-pressure change (see
core/base_state.py); theta_w is computed MESO-VIEW's way here.

    python scripts/validate_base_states.py --meso-view-data ~/Desktop/mesonet_data_viewer/data \
        --per-year 2 --out evidence/base_state_validation.csv
"""
import argparse
import pickle
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIELDS = ["pres", "temp", "dew", "rh", "qv", "th", "thv", "the", "thw", ("u_grid", "u"), ("v_grid", "v")]


def main() -> int:
    import numpy as np
    import pandas as pd
    from core import base_state
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--meso-view-data", type=Path, required=True)
    p.add_argument("--per-year", type=int, default=2, help="analysis times sampled per year")
    p.add_argument("--keys", nargs="*", help="specific YYYYMMDDHHMM keys instead of sampling")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", type=Path)
    a = p.parse_args()

    root = a.meso_view_data.expanduser()
    ref = pickle.load(open(root / "base_states_RAP_RUC" / "base_state_data_tsc5.pkl", "rb"))
    dbg = pd.concat([pd.read_csv(f, dtype={"time": str, "rap_init": str})
                     for f in sorted((root / "base_states_RAP_RUC" / "debug").glob("*_debug.csv"))])
    dbg = dbg[dbg["n_clusters"].fillna(0) > 0].drop_duplicates("time").set_index("time")
    keys = [k for k in dbg.index if isinstance(ref.get(k), dict)]
    if a.keys:
        keys = [k for k in a.keys if k in keys]
    else:
        rng = random.Random(a.seed)
        by_year = {}
        for k in keys:
            by_year.setdefault(k[:4], []).append(k)
        keys = sorted(k for ks in by_year.values() for k in rng.sample(ks, min(a.per_year, len(ks))))

    rows = []
    for key in keys:
        d = dbg.loc[key]
        t = datetime.strptime(key, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        mine = base_state.base_state_at(t, (float(d["clat"]), float(d["clon"])), float(d["Z"]),
                                        meso_view_theta_w=True)   # MESO-VIEW's theta_w method
        theirs = ref[key]
        row = {"key": key, "model": "RUC" if t < base_state.RAP_START else "RAP",
               "n_points_storm": mine.n_points if mine else 0, "n_points_meso_view": int(d["n_valid_points"])}
        for f in FIELDS:
            mine_f, theirs_f = (f, f) if isinstance(f, str) else f
            m = getattr(mine, mine_f) if mine else np.nan
            row[f"d_{theirs_f}"] = m - float(theirs[theirs_f]) if np.isfinite(m) else np.nan
        rows.append(row)
        print(key, row["model"], f"n {row['n_points_storm']}/{row['n_points_meso_view']}",
              " ".join(f"{k[2:]} {v:+.3g}" for k, v in row.items() if k.startswith("d_")), flush=True)

    out = pd.DataFrame(rows)
    print("\nlargest |difference| per field:")
    for c in [c for c in out.columns if c.startswith("d_")]:
        print(f"  {c[2:]:5s} {out[c].abs().max():.4g}   (median {out[c].abs().median():.3g})")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(a.out, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
