"""Keep one QApplication alive and dispose native widgets between tests."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def dispose_widgets(qapp):
    yield
    for widget in QApplication.topLevelWidgets():
        if widget.parent() is None:
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def isolated_fofs_index(monkeypatch, tmp_path):
    """No test reaches the live THREDDS catalog or the user's real index
    cache: loaders see no index (and fall back to each file's usual
    location, which tests fake). Tests that want an index install one."""
    from archive import fofs_index
    monkeypatch.setattr(fofs_index, "CACHE_PATH", tmp_path / "fofs_index.json")
    monkeypatch.setattr(fofs_index, "_current", None)
    monkeypatch.setattr(fofs_index, "get_index", lambda *a, **k: None)


LISTED_FAMILY = "Test listing"


@pytest.fixture
def listed_platform(monkeypatch):
    """A synthetic source scanned from two flat THREDDS folder listings
    (processed .nc and raw .txt) -- the generic listing behavior the
    availability index must keep, now that FOFS itself is scanned from the
    crawled file index instead. Call it with a name for more than one."""
    from archive import catalog as cat
    real = cat.catalogs_for_platform

    def catalogs(platform):
        if platform.family == LISTED_FAMILY:
            base = f"TEST/{platform.key}"
            return (cat.CatalogSpec(f"{base}/processed", ".nc"), cat.CatalogSpec(f"{base}/raw", ".txt"))
        return real(platform)

    monkeypatch.setattr(cat, "catalogs_for_platform", catalogs)
    monkeypatch.setattr(cat, "_LATEST_OBSERVED_YEAR", {
        **cat._LATEST_OBSERVED_YEAR,
        "LIST-mg1": 2015, "LIST-farfield": 2022, "LIST-probe1": 2026, "LIST-dltruck": 2026,
    })
    return lambda name="probe1": cat.KnownPlatform(f"LIST-{name}", name, LISTED_FAMILY, name, site=name)
