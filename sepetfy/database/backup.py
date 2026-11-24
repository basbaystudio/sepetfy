"""Veritabanı yedekleme.

SQLite: çalışan veritabanının tutarlı kopyası ``sqlite3`` backup API ile alınır
(WAL modunda da güvenlidir), gzip ile sıkıştırılır, en yeni ``keep`` yedek tutulur.
PostgreSQL: ``pg_dump`` kullanılır (Docker'da ``postgres`` profili, bkz. docs/SETUP.md).
"""

from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _sqlite_path(url: str) -> Path:
    return Path(url.split("///", 1)[1])


def backup_database(url: str, backup_dir: Path, keep: int = 14) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if url.startswith("sqlite"):
        src_path = _sqlite_path(url)
        if not src_path.exists():
            raise FileNotFoundError(f"Veritabanı bulunamadı: {src_path}")
        out = backup_dir / f"sepetfy-{stamp}.db.gz"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_db = Path(tmp) / "snap.db"
            src = sqlite3.connect(src_path)
            dst = sqlite3.connect(tmp_db)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            with open(tmp_db, "rb") as f_in, gzip.open(out, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
    elif url.startswith("postgresql"):
        out = backup_dir / f"sepetfy-{stamp}.sql.gz"
        pg_url = url.replace("+psycopg", "")
        with gzip.open(out, "wb") as f_out:
            proc = subprocess.run(["pg_dump", "--no-owner", pg_url], capture_output=True, check=False)
            if proc.returncode != 0:
                out.unlink(missing_ok=True)
                raise RuntimeError("pg_dump başarısız (ayrıntı için çıkış kodu: %d)" % proc.returncode)
            f_out.write(proc.stdout)
    else:
        raise ValueError("Desteklenmeyen veritabanı türü")

    _rotate(backup_dir, keep)
    log.info("Yedek alındı: %s", out.name)
    return out


def _rotate(backup_dir: Path, keep: int) -> None:
    backups = sorted(backup_dir.glob("sepetfy-*.gz"), key=lambda p: p.name, reverse=True)
    for old in backups[keep:]:
        old.unlink()
        log.info("Eski yedek silindi: %s", old.name)


def restore_sqlite(backup_file: Path, target_db: Path) -> None:
    """Bir .db.gz yedeğini hedef dosyaya açar (uygulama durdurulmuşken kullanın)."""
    with gzip.open(backup_file, "rb") as f_in, open(target_db, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
