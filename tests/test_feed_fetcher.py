from __future__ import annotations

import gzip
import io
import zipfile

import httpx
import pytest

from sepetfy.app.errors import ExternalServiceError
from sepetfy.collectors.feed.fetcher import DOWNLOADED, NOT_MODIFIED, UNCHANGED, fetch_feed

URL = "https://feeds.example.com/x.xml?token=SUPERSECRETTOKEN123"
BODY = b"<rss><item><id>1</id></item></rss>"


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_then_not_modified(tmp_path):
    def handler(req: httpx.Request):
        if req.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=BODY, headers={"ETag": '"v1"'})

    with client_for(handler) as c:
        r1 = fetch_feed(c, "t", URL, tmp_path)
        assert r1.status == DOWNLOADED and r1.path.read_bytes() == BODY and r1.etag == '"v1"'
        r2 = fetch_feed(c, "t", URL, tmp_path, etag=r1.etag, previous_hash=r1.sha256)
        assert r2.status == NOT_MODIFIED and r2.path is None
    assert not list(tmp_path.glob("*.download"))  # geçici dosya kalmadı


def test_same_content_detected_by_hash(tmp_path):
    with client_for(lambda req: httpx.Response(200, content=BODY)) as c:
        r1 = fetch_feed(c, "t", URL, tmp_path)
        r2 = fetch_feed(c, "t", URL, tmp_path, previous_hash=r1.sha256)
    assert r2.status == UNCHANGED


def test_gzip_and_zip_files(tmp_path):
    gz = gzip.compress(BODY)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("feed.xml", BODY)
    for payload in (gz, buf.getvalue()):
        with client_for(lambda req, p=payload: httpx.Response(200, content=p)) as c:
            r = fetch_feed(c, "t", URL, tmp_path)
        assert r.path.read_bytes() == BODY


@pytest.mark.parametrize("status,retryable", [(500, True), (503, True), (429, True), (404, False), (403, False)])
def test_http_errors(tmp_path, status, retryable):
    with client_for(lambda req: httpx.Response(status)) as c, pytest.raises(ExternalServiceError) as ei:
        fetch_feed(c, "t", URL, tmp_path)
    assert ei.value.retryable is retryable
    assert "SUPERSECRET" not in str(ei.value)


def test_network_error_does_not_leak_url(tmp_path):
    def handler(req):
        raise httpx.ConnectError(f"cannot connect to {req.url}")

    with client_for(handler) as c, pytest.raises(ExternalServiceError) as ei:
        fetch_feed(c, "t", URL, tmp_path)
    assert "SUPERSECRET" not in str(ei.value) and "ConnectError" in str(ei.value)


def test_size_limit(tmp_path):
    with client_for(lambda req: httpx.Response(200, content=b"x" * 5000)) as c, \
            pytest.raises(ExternalServiceError, match="boyut"):
        fetch_feed(c, "t", URL, tmp_path, max_bytes=1000)
    assert not list(tmp_path.iterdir())
