"""Offline case packages: a ZIP of a researcher's saved work for one archive
case plus the provenance needed to reproduce it -- not the source data
itself (first slice; data-carrying packages are a later decision).

Layout:
    package.json   format version, STORM version/commit, the case settings
                   (date, session window, clock, radar station/product/tilt,
                   velocity options), and provenance: every source file the
                   session loaded, with URL and SHA-256
    README.txt     the same, readable
    tracks/*.csv   the workspace's storm tracks for the date (MESO-VIEW format)

Opening a package imports its tracks into the recipient's workspace and
restores the case; the data is downloaded again from its sources, and the
recorded hashes show whether any upstream file has since changed.
"""
from __future__ import annotations

import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

FORMAT = "storm-case-package"
FORMAT_VERSION = 1
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


def build(path: Path, *, case: dict, tracks: list[Path], sources: list[dict], note: str = "") -> Path:
    """Write a package. `case` holds JSON-ready case settings (see MainWindow)."""
    path = Path(path)
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "storm": storm_version(),
        "case": case,
        "note": note,
        "tracks": [f"tracks/{Path(t).name}" for t in tracks],
        "sources": sources,
    }
    tmp = path.with_name(f".{path.name}.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("package.json", json.dumps(manifest, indent=2) + "\n")
        z.writestr("README.txt", _readme(manifest))
        for track in tracks:
            z.write(track, f"tracks/{Path(track).name}")
    tmp.replace(path)
    return path


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
    lines += ["", f"Source files loaded during the session ({len(m['sources'])}), with SHA-256:"]
    for s in m["sources"]:
        lines.append(f"  [{s['kind']}] {s['url']}")
        if s.get("sha256"):
            lines.append(f"      sha256 {s['sha256']}  {s.get('bytes') or '?'} bytes, loaded {s.get('loaded_at', '?')}")
    lines += ["", "The data itself is not included: STORM downloads it again from these sources.",
              "A different hash on re-download means the upstream file has changed since.", ""]
    return "\n".join(lines)
