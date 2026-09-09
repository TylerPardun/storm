"""Scoped HTTP evidence for the synchronous archive verification script.

Wrap actual fetcher requests without changing providers or retry behavior.
Credentials/headers are never recorded. This is diagnostic instrumentation,
not an application networking layer.
"""
from contextlib import ExitStack
import hashlib
import importlib
import logging
import time
from unittest.mock import patch
from urllib.error import HTTPError

_MODULES = ('vehicle_obs_archive_fetcher', 'mqtt_reader', 'clamps_sonde_archive_fetcher',
            'clamps_wind_archive_fetcher', 'clamps_surface_archive_fetcher',
            'clamps_tropoe_archive_fetcher', 'coptersonde_archive_fetcher')


class RequestEvidence:
    def __init__(self):
        self.requests = []
        self.warnings = []
        self._stack = ExitStack()

    def __enter__(self):
        owner = self
        class CaptureWarnings(logging.Handler):
            def emit(self, record):
                if record.name.startswith('archive.fetchers.'):
                    owner.warnings.append(record.getMessage())
        handler = CaptureWarnings(logging.WARNING)
        logging.getLogger().addHandler(handler)
        self._stack.callback(logging.getLogger().removeHandler, handler)
        for name in _MODULES:
            module = importlib.import_module('archive.fetchers.' + name)
            original = module.urlopen
            def traced(request, *args, _original=original, **kwargs):
                url = request.full_url if hasattr(request, 'full_url') else str(request)
                record = {'url': url, 'bytes_read': 0, 'complete_body': False}
                owner.requests.append(record)
                started = time.monotonic()
                try:
                    response = _original(request, *args, **kwargs)
                    record['status'] = getattr(response, 'status', 200)
                except Exception as exc:
                    record['status'] = exc.code if isinstance(exc, HTTPError) else None
                    record['error'] = str(exc)
                    record['elapsed_s'] = time.monotonic() - started
                    raise
                return _RecordedResponse(response, record, started)
            self._stack.enter_context(patch.object(module, 'urlopen', traced))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    def report(self):
        # A later successful retry resolves an earlier failure of the same URL.
        final = {r['url']: r for r in self.requests}
        errors = [r for r in final.values() if r.get('error') and r.get('status') != 404]
        return {'requests': self.requests, 'warnings': self.warnings,
                'retrieval_complete': not errors and not self.warnings,
                'unresolved_urls': [r['url'] for r in errors]}


class _RecordedResponse:
    def __init__(self, response, record, started):
        self.response, self.record, self.started = response, record, started
        self.digest = hashlib.sha256()

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        self.response.__enter__()
        return self

    def read(self, size=-1):
        try:
            data = self.response.read(size) if size != -1 else self.response.read()
        except Exception as exc:
            self.record['error'] = str(exc)
            raise
        self.digest.update(data)
        self.record['bytes_read'] += len(data)
        if size == -1 or not data:
            self.record['complete_body'] = True
        return data

    def __exit__(self, *exc):
        self.record['elapsed_s'] = time.monotonic() - self.started
        if self.record['complete_body']:
            self.record['sha256'] = self.digest.hexdigest()
        return self.response.__exit__(*exc)
