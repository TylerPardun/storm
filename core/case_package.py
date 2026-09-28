"""Case packages: a ZIP of one archive case -- the researcher's storm
tracks, the case settings, the provenance of every source file the session
loaded and, for the data types the user chose, the data files themselves.

Layout:
    package.json   format version, STORM version/commit, the case settings
                   (date, session window, clock, radar station/product/tilt,
                   velocity options), provenance for every loaded file (URL,
                   SHA-256, whether it's included) and the included files
    README.txt     the same, readable
    tracks/*.csv   the workspace's storm tracks for the date (MESO-VIEW format)
    data/<type>/*  included source files, byte for byte as downloaded

Opening a package imports its tracks, restores the case, and serves the
included files to STORM before any network request (core/package_sources.py);
whatever wasn't included is downloaded from its source as usual.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

FORMAT = "storm-case-package"
FORMAT_VERSION = 2          # 2: can carry data files; 1 (tracks + provenance only) still opens

# The data types a package can carry, in the order they're offered; keys are
# the provenance kinds recorded by core/package_sources.py.
DATA_TYPES = {
    "radar": "WSR-88D radar volumes",
    "mesonet": "Mobile mesonet (one-second FOFS)",
    "mqtt history": "Recorded vehicles, annotations and scan sectors",
    "clamps surface": "CLAMPS surface met",
    "clamps profiles": "CLAMPS winds, sondes and TROPoe",
    "raw lidar": "Raw lidar scans",
    "noxp radar": "NOXP mobile radar",
    "coptersonde": "CopterSonde profiles",
    "soundings": "Soundings (HRRR / observed)",
    "satellite": "Satellite imagery",
    "asos": "ASOS observations",
    "hazards": "SPC / NWS hazards",
    "damage paths": "Damage paths",
    "listings": "Other catalog listings",
}
_ROOT = Path(__file__).resolve().parents[1]


def storm_version() -> dict:
    """STORM's release version and, when run from a git checkout, the commit."""
    version = {"version": (_ROOT / "VERSION").read_text().strip() if (_ROOT / "VERSION").exists() else ""}
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True,
                                text=True, timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=_ROOT,
                               capture_output=True, text=True, timeout=5).stdout.strip() != ""
        version.update(commit=commit, uncommitted_changes=dirty)
    except (OSError, subprocess.SubprocessError):
        pass
    return version


def _member_name(entry: dict, digest: str) -> str:
    kind = entry.get("kind") or "other"
    tail = PurePosixPath(entry["url"].split("?", 1)[0]).name or "file"
    return f"data/{kind.replace(' ', '_')}/{digest[:16]}_{tail[-80:]}"


def build(path: Path, *, case: dict, tracks: list[Path], sources: list[dict], note: str = "",
          include_kinds: set[str] = frozenset(), fetch=None, progress=None, cancel=None) -> Path:
    """Write a package. `case` holds JSON-ready case settings (see MainWindow).
    Files of the kinds in `include_kinds` are packed: `fetch(url)` returns
    their bytes (the session's kept copy or a fresh download); a download
    whose hash differs from what was viewed is packed but marked as changed.
    `progress(done, total, label)` reports and `cancel` (an Event) stops."""
    path = Path(path)
    included, changed = [], 0
    wanted = [e for e in sources if e.get("kind") in include_kinds]
    tmp = path.with_name(f".{path.name}.part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for i, entry in enumerate(wanted):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("export canceled")
                if progress:
                    progress(i, len(wanted), entry["url"].rsplit("/", 1)[-1][:60])
                try:
                    data = fetch(entry["url"])
                except Exception as exc:  # noqa: BLE001 -- record and carry on
                    included.append({**entry, "member": None, "error": str(exc)})
                    continue
                digest = hashlib.sha256(data).hexdigest()
                member = _member_name(entry, digest)
                # radar volumes, netCDF and gzip are compressed already
                z.writestr(member, data, compress_type=zipfile.ZIP_STORED if len(data) > 1_000_000
                           else zipfile.ZIP_DEFLATED)
                record = {"kind": entry.get("kind"), "url": entry["url"], "sha256": digest,
                          "bytes": len(data), "member": member}
                if entry.get("sha256") and entry["sha256"] != digest:
                    record["viewed_sha256"] = entry["sha256"]
                    changed += 1
                included.append(record)
            manifest = _manifest(case, tracks, sources, note, included, include_kinds, changed)
            z.writestr("package.json", json.dumps(manifest, indent=2) + "\n")
            z.writestr("README.txt", _readme(manifest))
            for track in tracks:
                z.write(track, f"tracks/{Path(track).name}")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    if progress:
        progress(len(wanted), len(wanted), "done")
    return path


def _manifest(case, tracks, sources, note, included, include_kinds, changed) -> dict:
    packed = {e["url"] for e in included if e.get("member")}
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "storm": storm_version(),
        "case": case,
        "note": note,
        "tracks": [f"tracks/{Path(t).name}" for t in tracks],
        "included_data_types": sorted(include_kinds),
        "data": [e for e in included if e.get("member")],
        "data_not_packed": [e for e in included if not e.get("member")],
        "data_changed_since_viewed": changed,
        "sources": [{**s, "included": s["url"] in packed} for s in sources],
    }


def read(path: Path) -> tuple[dict, dict[str, bytes]]:
    """(manifest, {track file name: bytes}). Raises ValueError for anything
    that isn't a readable package of a version this STORM understands."""
    try:
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("package.json"))
            if manifest.get("format") != FORMAT:
                raise ValueError("not a STORM case package")
            if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
                raise ValueError("made by a newer STORM; update STORM to open it")
            for entry in manifest.get("data", []):
                name = PurePosixPath(entry.get("member") or "")
                if name.parts[:1] != ("data",) or ".." in name.parts:
                    raise ValueError(f"unexpected entry {entry.get('member')!r}")
            tracks = {}
            for member in manifest.get("tracks", []):
                name = PurePosixPath(member)
                if name.parent != PurePosixPath("tracks") or name.suffix.lower() != ".csv":
                    raise ValueError(f"unexpected entry {member!r}")      # no paths outside tracks/
                tracks[name.name] = z.read(member)
            return manifest, tracks
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a readable STORM case package ({exc})") from exc


def _readme(m: dict) -> str:
    case, storm = m["case"], m["storm"]
    lines = [
        "STORM case package",
        "==================",
        f"Created {m['created_at']} with STORM {storm.get('version', '?')}"
        + (f" (commit {storm['commit'][:10]}{', with uncommitted changes' if storm.get('uncommitted_changes') else ''})"
           if storm.get("commit") else ""),
        "",
        f"Archive date:   {case.get('session_date')}",
        f"Session window: {case.get('window_start')} to {case.get('window_end')}",
        f"Clock time:     {case.get('clock_time')}",
        f"Radar:          {case.get('radar_station') or '—'} {case.get('radar_product') or ''}"
        f" (tilt index {case.get('radar_tilt_index')})",
    ]
    velocity = case.get("velocity") or {}
    if velocity.get("dealias") or velocity.get("storm_relative"):
        lines.append(f"Velocity:       dealias={velocity.get('dealias')} storm-relative={velocity.get('storm_relative')}")
    if m.get("note"):
        lines += ["", "Note:", m["note"]]
    lines += ["", "Storm tracks:"] + ([f"  {t}" for t in m["tracks"]] or ["  (none)"])
    data = m.get("data", [])
    if data:
        lines += ["", "Data included in this package:"]
        for kind in sorted({d["kind"] for d in data}):
            files = [d for d in data if d["kind"] == kind]
            mb = sum(d["bytes"] or 0 for d in files) / 1e6
            lines.append(f"  {DATA_TYPES.get(kind, kind)}: {len(files)} files, {mb:.1f} MB")
        if m.get("data_changed_since_viewed"):
            lines.append(f"  ({m['data_changed_since_viewed']} file(s) changed upstream since the case was viewed; "
                         "both hashes are in package.json)")
    left_out = sorted({s["kind"] for s in m["sources"] if not s.get("included")})
    if left_out:
        lines += ["", "Not included (downloaded from their sources when the package is opened): "
                  + ", ".join(DATA_TYPES.get(k, k) for k in left_out)]
    lines += ["", f"Source files loaded during the session ({len(m['sources'])}), with SHA-256:"]
    for s in m["sources"]:
        lines.append(f"  [{s['kind']}]{' (included)' if s.get('included') else ''} {s['url']}")
        if s.get("sha256"):
            lines.append(f"      sha256 {s['sha256']}  {s.get('bytes') or '?'} bytes, loaded {s.get('loaded_at', '?')}")
    lines += ["", "Opening this package in STORM uses the included files first and downloads anything else",
              "from its source. A different hash on re-download means the upstream file has changed.", ""]
    return "\n".join(lines)
