import io
import json
from threading import Event

import pytest

from archive.catalog import ScanCanceled
from archive.fetchers import noxp_archive_fetcher as noxp


def test_canceled_download_never_leaves_reusable_partial_file(monkeypatch, tmp_path):
    cancel = Event()
    class Response(io.BytesIO):
        def read(self, n=-1):
            chunk = super().read(n)
            cancel.set()
            return chunk
    monkeypatch.setattr(noxp, 'urlopen', lambda *a, **k: Response(b'partial bytes'))
    with pytest.raises(ScanCanceled):
        noxp.download_asset(noxp.DATA_ROOT + 'file.nc', tmp_path, cancel, 'file.nc')
    assert not list(tmp_path.iterdir())


def test_cache_verifies_content_identity_before_reuse(monkeypatch, tmp_path):
    calls = []
    def request(*a, **k):
        calls.append(1)
        return io.BytesIO(b'scientific file')
    monkeypatch.setattr(noxp, 'urlopen', request)
    args = (noxp.DATA_ROOT + 'file.nc', tmp_path, Event(), 'file.nc')
    path, record = noxp.download_asset(*args)
    assert record['bytes'] == 15
    assert noxp.download_asset(*args)[1]['sha256'] == record['sha256']
    assert len(calls) == 1
    path.write_bytes(b'corrupt')
    noxp.download_asset(*args)
    assert path.read_bytes() == b'scientific file'
    assert len(calls) == 2


def test_size_limit_cleans_up_partial_download(monkeypatch, tmp_path):
    monkeypatch.setattr(noxp, 'urlopen', lambda *a, **k: io.BytesIO(b'too large'))
    with pytest.raises(ValueError, match='size/time'):
        noxp.download_asset(noxp.DATA_ROOT + 'file.nc', tmp_path, Event(), 'file.nc', max_bytes=2)
    assert not list(tmp_path.iterdir())
