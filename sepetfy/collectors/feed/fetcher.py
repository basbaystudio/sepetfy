"""Feed indirici.

1. Koşullu istek: önceki ETag / Last-Modified gönderilir; sunucu 304 derse hiçbir şey indirilmez.
2. Dosya diske parça parça yazılır, aynı anda SHA-256 özeti hesaplanır; boyut sınırı uygulanır.
3. Özet bir öncekiyle aynıysa "değişmedi" denir ve dosya atılır.
4. gzip / zip ise açılır.

GÜVENLİK: Feed URL'si kişiye özel token içerir. Hata mesajlarına ve loglara URL YAZILMAZ.
"""

from __future__ import annotations

import gzip
import hashlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from sepetfy.app.errors import ExternalServiceError

NOT_MODIFIED = "not_modified"
UNCHANGED = "unchanged"
DOWNLOADED = "downloaded"

USER_AGENT = "SepetfyFeedBot/0.1"


@dataclass
class FetchResult:
    status: str
    path: Path | None = None
    etag: str | None = None
    last_modified: str | None = None
    sha256: str | None = None
    size: int = 0


def _service(name: str) -> str:
    return f"feed:{name}"


def fetch_feed(
    client: httpx.Client,
    name: str,
    url: str,
    work_dir: Path,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    previous_hash: str | None = None,
    max_bytes: int = 500_000_000,
    timeout_sec: float = 120,
) -> FetchResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    fd, tmp_name = tempfile.mkstemp(prefix=f"{name}-", suffix=".download", dir=work_dir)
    tmp = Path(tmp_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with open(fd, "wb") as out:
            try:
                with client.stream("GET", url, headers=headers, timeout=timeout_sec,
                                   follow_redirects=True) as resp:
                    if resp.status_code == 304:
                        tmp.unlink(missing_ok=True)
                        return FetchResult(NOT_MODIFIED, etag=etag, last_modified=last_modified,
                                           sha256=previous_hash)
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise ExternalServiceError(_service(name), f"HTTP {resp.status_code}", retryable=True)
                    if resp.status_code >= 400:
                        raise ExternalServiceError(_service(name), f"HTTP {resp.status_code}", retryable=False)
                    for chunk in resp.iter_bytes(1 << 16):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ExternalServiceError(_service(name), f"boyut sınırı aşıldı (> {max_bytes} bayt)",
                                                       retryable=False)
                        digest.update(chunk)
                        out.write(chunk)
                    new_etag = resp.headers.get("ETag")
                    new_lm = resp.headers.get("Last-Modified")
            except httpx.HTTPError as exc:
                # httpx mesajları URL (token) içerebilir; yalnızca hata türünü aktar
                raise ExternalServiceError(_service(name), type(exc).__name__, retryable=True) from None

        sha = digest.hexdigest()
        if previous_hash and sha == previous_hash:
            tmp.unlink(missing_ok=True)
            return FetchResult(UNCHANGED, etag=new_etag, last_modified=new_lm, sha256=sha, size=size)

        xml_path = _decompress(tmp, work_dir / f"{name}.incoming.xml", max_bytes)
        return FetchResult(DOWNLOADED, path=xml_path, etag=new_etag, last_modified=new_lm, sha256=sha, size=size)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _decompress(src: Path, dest: Path, max_bytes: int) -> Path:
    with open(src, "rb") as f:
        magic = f.read(4)
    if magic[:2] == b"\x1f\x8b":
        with gzip.open(src, "rb") as f_in, open(dest, "wb") as f_out:
            _copy_limited(f_in, f_out, max_bytes * 10)
        src.unlink()
    elif magic == b"PK\x03\x04":
        with zipfile.ZipFile(src) as zf:
            members = [m for m in zf.infolist() if m.filename.lower().endswith(".xml") and not m.is_dir()]
            if len(members) != 1:
                raise ExternalServiceError("feed", "zip içinde tek bir .xml bekleniyordu", retryable=False)
            with zf.open(members[0]) as f_in, open(dest, "wb") as f_out:
                _copy_limited(f_in, f_out, max_bytes * 10)
        src.unlink()
    else:
        shutil.move(src, dest)
    return dest


def _copy_limited(f_in, f_out, limit: int) -> None:
    total = 0
    while chunk := f_in.read(1 << 16):
        total += len(chunk)
        if total > limit:
            raise ExternalServiceError("feed", "açılmış dosya boyut sınırını aştı", retryable=False)
        f_out.write(chunk)
