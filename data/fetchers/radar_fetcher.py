
import logging
import re
import threading
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urljoin
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

from PyQt6.QtCore import QObject, pyqtSignal

from archive import thredds_paths as paths
log = logging.getLogger(__name__)

# unidata THREDDS Level 3 catalog — public, no token required
THREDDS_CATALOG_ROOT    = f"{paths.catalog_root(paths.UCAR)}/{paths.UCAR_NEXRAD_LEVEL3}"
THREDDS_FILESERVER_ROOT = paths.file_root(paths.UCAR)
REQUEST_TIMEOUT_SECONDS = 20

# poll every 2 minutes — Level 3 scans update on a 5-6 minute cycle
POLL_INTERVAL = 120

# how many historical scans to download on first fetch per product (6 ref + 6 vel = 12 total)
BACKFILL_COUNT = 6


class RadarFetcher(QObject):
    """
    Background thread that polls THREDDS catalogs for new NEXRAD Level 3 scans.

    Signals:
        new_data(site, product, bytes)  — emitted when a new scan file is downloaded
        fetch_error(str)                — emitted on recoverable errors
    """

    new_data    = pyqtSignal(str, str, bytes)   # site, product, raw file bytes
    fetch_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._site:     Optional[str] = None
        self._products: list[str]     = []
        self._running                 = False
        self._thread:   Optional[threading.Thread] = None

        # track the last dataset key per product so we don't re-download the same file
        self._last_key: dict[str, str] = {}

        # lock prevents overlapping fetches — poll_loop uses blocking acquire,
        self._fetch_lock = threading.Lock()

        # event lets stop() interrupt a sleeping poll loop immediately instead
        self._stop_event = threading.Event()


    def set_site(self, site: str):
        """change the radar site; takes effect on next poll cycle."""
        self._site = _normalize_site(site)
        self._last_key.clear()
        log.info("site set to %s", self._site)

    def set_products(self, products: list[str]):
        """set which products to fetch (e.g. ['N0Q', 'N0U'])."""
        self._products = [p.upper() for p in products]
        self._last_key.clear()
        log.info("products set to %s", self._products)

    def start(self):
        """start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log.info("fetcher started")

    def stop(self):
        """stop the background polling thread (returns immediately; thread exits within seconds)."""
        self._running = False
        self._stop_event.set()   # wake the sleeping poll loop so it exits promptly
        log.info("fetcher stopped")

    def reset_history(self):
        """clear download history so the next fetch triggers a full backfill."""
        self._last_key.clear()
        log.debug("download history cleared")


    def _poll_loop(self):
        """Main polling loop — runs in background thread.

        Uses Event.wait() instead of time.sleep() so stop() wakes the loop
        immediately rather than waiting up to POLL_INTERVAL seconds.
        """
        self._stop_event.clear()
        log.info("poll loop started (interval=%ds)", POLL_INTERVAL)
        while self._running:
            if self._site and self._products:
                # blocking acquire — poll_loop owns the cycle, no need to skip
                with self._fetch_lock:
                    for product in self._products:
                        try:
                            self._fetch_latest(self._site, product)
                        except Exception as e:
                            log.warning("fetch error (%s/%s): %s", self._site, product, e)
                            self.fetch_error.emit(str(e))
            # wait up to POLL_INTERVAL seconds, but wake immediately if stop() is called
            self._stop_event.wait(POLL_INTERVAL)
            self._stop_event.clear()

    def _fetch_latest(self, site: str, product: str):
        """find newest dataset for site/product and download it if new.

        On the first fetch after a site/product change (_last_key is empty for
        this product) we backfill BACKFILL_COUNT scans so the playback controls
        are immediately usable.  On subsequent polls we just grab the newest.
        """
        is_first_fetch = product not in self._last_key

        if is_first_fetch:
            entries = self._list_recent_datasets(site, product, BACKFILL_COUNT)
            if not entries:
                log.debug("backfill: no datasets found for %s/%s", site, product)
                return
            for dataset_id, dataset_url in entries:
                log.info("backfill downloading %s", dataset_id)
                data = self._download_url(dataset_url)
                if data:
                    self._last_key[product] = dataset_id
                    self.new_data.emit(site, product, data)
                    log.info("backfill downloaded %s (%d bytes)", dataset_id, len(data))
            return

        # normal poll — just grab the latest
        dataset_id, dataset_url = self._latest_dataset_for_site_product(site, product)
        if not dataset_id or not dataset_url:
            log.debug("no objects found for %s/%s", site, product)
            return

        if self._last_key.get(product) == dataset_id:
            log.debug("skipping fetch — same dataset: %s", dataset_id)
            return

        log.info("downloading %s", dataset_id)
        data = self._download_url(dataset_url)
        if not data:
            return

        self._last_key[product] = dataset_id
        self.new_data.emit(site, product, data)
        log.info("downloaded %s (%d bytes)", dataset_id, len(data))

    def _list_recent_datasets(
        self, site: str, product: str, n: int
    ) -> list[tuple[str, str]]:
        """Return up to n (dataset_id, dataset_url) pairs, sorted oldest-first.

        Checks the two most-recent day catalogs so calls near midnight still
        find enough entries.
        """
        site_token = _thredds_site_token(site)
        for product_code in _product_aliases(product.upper()):
            site_catalog_url = (
                f"{THREDDS_CATALOG_ROOT}/{product_code}/{site_token}/catalog.xml"
            )
            site_xml = self._read_xml(site_catalog_url)
            if site_xml is None:
                continue

            day_catalogs = _extract_day_catalog_urls(site_xml, site_catalog_url)
            if not day_catalogs:
                continue

            all_entries: list[tuple[str, str, str]] = []  # (name, url_path, product_code)
            for day_catalog_url in sorted(day_catalogs, reverse=True)[:2]:
                day_xml = self._read_xml(day_catalog_url)
                if day_xml is None:
                    continue
                for name, url_path in _extract_dataset_entries(day_xml):
                    all_entries.append((name, url_path, product_code))
                if len(all_entries) >= n:
                    break

            if not all_entries:
                continue

            # sort newest-first by dataset name (which encodes the timestamp),
            all_entries.sort(key=lambda e: e[0], reverse=True)
            recent = all_entries[:n]
            recent.reverse()

            result = []
            for name, url_path, pc in recent:
                dataset_url = f"{THREDDS_FILESERVER_ROOT}/{url_path.lstrip('/')}"
                dataset_id  = f"{pc}:{name}"
                result.append((dataset_id, dataset_url))
            return result

        log.info("no THREDDS catalog entries for %s/%s", site, product)
        return []

    def fetch_now(self):
        """trigger an immediate fetch outside the normal poll cycle."""
        if not self._site or not self._products:
            return
        site = self._site
        products = list(self._products)

        # non-blocking tryacquire — skip if poll_loop is already mid-fetch
        if not self._fetch_lock.acquire(blocking=False):
            return

        def _run():
            try:
                for product in products:
                    try:
                        self._fetch_latest(site, product)
                    except Exception as e:
                        log.warning("immediate fetch error: %s", e)
                        self.fetch_error.emit(str(e))
            finally:
                self._fetch_lock.release()

        threading.Thread(target=_run, daemon=True).start()

    def _latest_dataset_for_site_product(self, site: str, product: str) -> tuple[str, str]:
        """
        Return (dataset_id, dataset_url) for the latest scan.
        THREDDS layout: /{product}/{site_without_K}/{yyyymmdd}/catalog.xml
        """
        site_token = _thredds_site_token(site)
        for product_code in _product_aliases(product.upper()):
            site_catalog_url = (
                f"{THREDDS_CATALOG_ROOT}/{product_code}/{site_token}/catalog.xml"
            )
            site_xml = self._read_xml(site_catalog_url)
            if site_xml is None:
                continue

            day_catalogs = _extract_day_catalog_urls(site_xml, site_catalog_url)
            if not day_catalogs:
                continue

            # check the two most-recent day catalogs in case today's is empty
            for day_catalog_url in sorted(day_catalogs, reverse=True)[:2]:
                day_xml = self._read_xml(day_catalog_url)
                if day_xml is None:
                    continue

                datasets = _extract_dataset_entries(day_xml)
                if not datasets:
                    continue

                datasets.sort(key=lambda d: d[0], reverse=True)
                dataset_name, url_path = datasets[0]
                dataset_url = f"{THREDDS_FILESERVER_ROOT}/{url_path.lstrip('/')}"
                dataset_id  = f"{product_code}:{dataset_name}"
                return dataset_id, dataset_url

        self.fetch_error.emit(f"Radar catalog failed for {site}/{product}")
        return "", ""

    def _download_url(self, url: str) -> bytes:
        """download dataset bytes from a public URL."""
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            self.fetch_error.emit(f"Radar download failed: {exc}")
            return b""

    def _read_xml(self, url: str) -> Optional[ET.Element]:
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
            return ET.fromstring(xml_text)
        except (HTTPError, URLError, TimeoutError, ET.ParseError) as exc:
            log.debug("catalog read failed (%s): %s", url, exc)
            return None



def _normalize_site(site: str) -> str:
    text = (site or "").strip().upper()
    match = re.search(r"\b[A-Z][A-Z0-9]{3}\b", text)
    return match.group(0) if match else ""


def _product_aliases(product: str) -> tuple[str, ...]:
    if product in ("N0Q", "N0B"):   # reflectivity family — try super-res first
        return ("N0B", "N0Q", "N0R")
    if product == "N0U":   # velocity family — NBU is the super-res fallback when N0U is absent
        return ("N0U", "NBU", "N0S")
    return (product,)


def _thredds_site_token(site: str) -> str:
    # thredds uses 3-letter IDs for most sites (e.g., INX instead of KINX)
    site = site.upper()
    if site.startswith("K") and len(site) == 4:
        return site[1:]
    return site


def _extract_day_catalog_urls(root: ET.Element, base_url: str) -> list[str]:
    ns = {
        "cat":   paths.THREDDS_NS,
        "xlink": "http://www.w3.org/1999/xlink",
    }
    out: list[str] = []
    for ref in root.findall(".//cat:catalogRef", ns):
        href  = ref.attrib.get("{http://www.w3.org/1999/xlink}href", "")
        title = ref.attrib.get("{http://www.w3.org/1999/xlink}title", "")
        # title is the date string — only include yyyymmdd entries
        if re.fullmatch(r"\d{8}", title) and href:
            out.append(urljoin(base_url, href))
    return out


def _extract_dataset_entries(root: ET.Element) -> list[tuple[str, str]]:
    ns = {"cat": paths.THREDDS_NS}
    datasets: list[tuple[str, str]] = []
    for ds in root.findall(".//cat:dataset", ns):
        url_path = ds.attrib.get("urlPath", "")
        name     = ds.attrib.get("name", "")
        if url_path and name:
            datasets.append((name, url_path))
    return datasets
