"""Check that every data source STORM reads still answers.

THREDDS (archive/thredds_paths.py): asks each folder for its catalog, one at
a time with a pause between (data.nssl.noaa.gov throttles bursts). For a
folder that no longer answers, it lists what the nearest parent folder that
does answer now holds, so a renamed or moved folder is easy to spot.

AWS buckets and web APIs (data/endpoints.py): one cheap request each. An
API that answers 4xx without parameters or a key still exists; 404/410 or
no answer means it moved or is gone.

Then update the file named in the report. THREDDS is fragile under load:
the checker asks one folder at a time, 1.5 s apart, and stops if THREDDS
stops answering. Don't run it in a loop or alongside a STORM session that is
loading archive data.

    python scripts/check_data_sources.py            # everything (~90 checks, about three minutes)
    python scripts/check_data_sources.py clamps     # only checks whose description or address has "clamps"

Exit status: 0 when every folder answers, 1 otherwise. Network only; it
writes nothing.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import net_compat  # noqa: E402
from archive import thredds_paths as paths  # noqa: E402
import config  # noqa: E402
from data import endpoints  # noqa: E402
from archive.fetchers.vehicle_obs_archive_fetcher import _ssl_context  # noqa: E402 -- NSSL's WAF needs its setup

PAUSE_S = 1.5                 # between THREDDS requests: it throttles, and can stall, under bursts
API_PAUSE_S = 0.4
GIVE_UP_AFTER = 3             # THREDDS silent this many times in a row: stop asking and let it recover
TIMEOUT_S = 15
_USER_AGENT = "Mozilla/5.0 STORM/1.0 (path check)"
_CHILD_RE = re.compile(r'href=["\']([^"\'?#]+)/catalog\.(?:html|xml)["\']')


def fetch(url: str) -> tuple[int | None, str]:
    """(HTTP status, body), or (None, reason) when there was no answer."""
    try:
        # one attempt, no retries: a health check shouldn't add to THREDDS's load
        with urlopen(Request(url, headers={"User-Agent": _USER_AGENT}), timeout=TIMEOUT_S, context=_ssl_context()) as r:
            return r.status, r.read(2_000_000).decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, ""
    except (URLError, TimeoutError, OSError) as exc:
        return None, str(getattr(exc, "reason", exc))


def children(body: str) -> list[str]:
    """Subfolder names in a catalog page."""
    names = {m.rstrip("/").rsplit("/", 1)[-1] for m in _CHILD_RE.findall(body)}
    return sorted(n for n in names if n and n != "catalog")


def nearest_parent(host: str, path: str) -> tuple[str, list[str]] | None:
    """The closest ancestor folder that answers, and what it holds."""
    parts = path.strip("/").split("/")
    for n in range(len(parts) - 1, 0, -1):
        parent = "/".join(parts[:n])
        time.sleep(PAUSE_S)
        status, body = fetch(paths.catalog_url(parent, host))
        if status == 200:
            return parent, children(body)
    return None


def main(argv: list[str]) -> int:
    net_compat.prefer_ipv4()
    wanted = argv[1].lower() if len(argv) > 1 else ""
    folders = [f for f in paths.all_folders() if wanted in f[0].lower() or wanted in f[2].lower()]
    missing, unreachable, absent_paths = [], [], set()
    if folders:
        print(f"Checking {len(folders)} THREDDS folders (archive/thredds_paths.py)…\n")
    silent_run = 0
    for i, (what, host, path, required) in enumerate(folders):
        if silent_run >= GIVE_UP_AFTER:
            print(f"\n  THREDDS stopped answering ({silent_run} in a row). It recovers on its own once left alone;\n"
                  f"  stopping here ({len(folders) - i} folders not checked). Try again in 10-15 minutes.")
            unreachable += [(w, h, p, r) for w, h, p, r in folders[i:]]
            break
        status, body = fetch(paths.catalog_url(path, host))
        silent_run = silent_run + 1 if status is None else 0
        if status == 200:
            print(f"  ok       {what:42s} {path}")
        elif status == 404 and not required:
            print(f"  absent   {what:42s} {path}  (optional)")
            absent_paths.add(path)
        elif status is None:
            print(f"  NO REPLY {what:42s} {path}  ({body})")
            unreachable.append((what, host, path, required))
        else:
            print(f"  MISSING  {what:42s} {path}  (HTTP {status})")
            missing.append((what, host, path, required))
        time.sleep(PAUSE_S)

    for what, host, path, _required in (missing if silent_run < GIVE_UP_AFTER else []):
        found = nearest_parent(host, path)
        print(f"\n{what}: {path} is gone.")
        if found is None:
            print("  None of its parent folders answer either.")
        else:
            parent, names = found
            print(f"  {parent}/ now holds: {', '.join(names) if names else '(no subfolders)'}")
    if unreachable:
        print(f"\n{len(unreachable)} folder(s) didn't answer at all: the server may be down or throttling. "
              "Try again in a few minutes.")
    for sid, d, _u in paths.CLAMPS_TROPOE_PLATFORMS:      # each trailer needs one TROPoe variant
        tried = [f for f in folders if f[0].startswith(f"TROPoe {sid} ")]
        if tried and all(f[2] in absent_paths for f in tried):
            missing.append((f"TROPoe {sid} (every variant)", paths.NSSL, f"{paths.CLAMPS}/{d}/processed", True))
    api_missing, api_silent = check_endpoints(wanted)
    ok = not missing and not unreachable and not api_missing and not api_silent
    if ok:
        print("\nEverything answers.")
    else:
        if missing or unreachable:
            print(f"\nTHREDDS: {len(missing)} missing, {len(unreachable)} without a reply -> archive/thredds_paths.py")
        if api_missing or api_silent:
            print(f"AWS/APIs: {len(api_missing)} missing, {len(api_silent)} without a reply -> data/endpoints.py")
    return 0 if ok else 1


_STILL_THERE = {400, 401, 403, 405, 422, 429}      # answers, but wants parameters or a key


def probe(url: str) -> tuple[int | None, str]:
    import requests
    headers = {"User-Agent": _USER_AGENT}
    if url.startswith(config.NSSL_API_ROOT) and config.NSSL_API_KEY:
        headers["X-API-Key"] = config.NSSL_API_KEY
    try:
        with requests.get(url, headers=headers, timeout=TIMEOUT_S, stream=True) as r:
            return r.status_code, ""
    except requests.exceptions.SSLError:
        # an invalid certificate: still find out whether the address exists
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            with requests.get(url, headers=headers, timeout=TIMEOUT_S, stream=True, verify=False) as r:
                return r.status_code, "bad certificate"
        except requests.RequestException as exc:
            return None, f"bad certificate, then {type(exc).__name__}"
    except requests.RequestException as exc:
        return None, type(exc).__name__


def check_endpoints(wanted: str) -> tuple[list[str], list[str]]:
    checks = [(w, u) for w, u in endpoints.all_endpoints() if wanted in w.lower() or wanted in u.lower()]
    if not checks:
        return [], []
    print(f"\nChecking {len(checks)} AWS buckets and web APIs (data/endpoints.py)…\n")
    missing, silent = [], []
    for what, url in checks:
        status, reason = probe(url)
        short = url.split("?", 1)[0]
        if status is not None and (status < 400 or status in _STILL_THERE):
            note = "" if status < 400 else f"  (HTTP {status}: answers, wants parameters or a key)"
            note += f"  ({reason})" if reason else ""
            print(f"  ok       {what:32s} {short}{note}")
        elif status in (404, 410):
            print(f"  MISSING  {what:32s} {short}  (HTTP {status}{', ' + reason if reason else ''})")
            missing.append(what)
        else:
            print(f"  NO REPLY {what:32s} {short}  ({reason or f'HTTP {status}'})")
            silent.append(what)
        time.sleep(API_PAUSE_S)
    return missing, silent


if __name__ == "__main__":
    sys.exit(main(sys.argv))
