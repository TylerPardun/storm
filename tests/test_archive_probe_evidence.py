import hashlib
import io
from urllib.error import HTTPError

from archive.fetchers import mqtt_reader
from scripts.archive_probe_evidence import RequestEvidence


def test_evidence_records_real_content_without_auth_headers(monkeypatch):
    monkeypatch.setattr(mqtt_reader, 'urlopen', lambda *a, **k: io.BytesIO(b'actual file'))
    with RequestEvidence() as evidence:
        assert mqtt_reader._fetch_text('https://example.test/file') == 'actual file'
    record = evidence.report()['requests'][0]
    assert record['bytes_read'] == 11
    assert record['sha256'] == hashlib.sha256(b'actual file').hexdigest()
    assert 'headers' not in record


def test_evidence_distinguishes_timeout_from_clean_404(monkeypatch):
    def fail(request, **kwargs):
        if request.full_url.endswith('404'):
            raise HTTPError(request.full_url, 404, 'not found', {}, None)
        raise TimeoutError('timeout')
    monkeypatch.setattr(mqtt_reader, 'urlopen', fail)
    with RequestEvidence() as evidence:
        assert mqtt_reader._fetch_text('https://example.test/404') is None
        try:
            mqtt_reader._fetch_text('https://example.test/slow')
        except TimeoutError:
            pass
    assert evidence.report()['unresolved_urls'] == ['https://example.test/slow']
    assert not evidence.report()['retrieval_complete']
