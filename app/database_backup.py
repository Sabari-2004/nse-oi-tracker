"""Optional bounded SQLite snapshots for ephemeral Render storage."""
from __future__ import annotations
import gzip
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
_TIMEOUT = 15
_KEEP = 7

def _config():
    base = os.getenv("NSE_OI_BACKUP_URL", "").strip().rstrip("/")
    token = os.getenv("NSE_OI_BACKUP_TOKEN", "").strip()
    return base, token

def _request(url: str, *, method: str = "GET", body: bytes | None = None, token: str = "") -> bytes:
    headers = {"User-Agent": "nse-oi-tracker-backup/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read()

def _manifest_url(base: str) -> str:
    return f"{base}/manifest.json"

def upload_database_snapshot(database_path: Path) -> bool:
    """Upload one compressed SQLite snapshot; failures are deliberately swallowed."""
    base, token = _config()
    if not base or not Path(database_path).exists():
        return False
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"nse-oi-{stamp}.sqlite3.gz"
        with tempfile.NamedTemporaryFile(suffix=".gz") as temporary:
            with gzip.open(temporary.name, "wb", compresslevel=6) as output, open(database_path, "rb") as source:
                shutil.copyfileobj(source, output)
            temporary.seek(0)
            payload = temporary.read()
        _request(f"{base}/{name}", method="PUT", body=payload, token=token)
        try:
            manifest = json.loads(_request(_manifest_url(base), token=token) or b"[]")
            names = [x for x in manifest if isinstance(x, str) and x != name]
        except Exception:
            names = []
        names = [name] + names[: _KEEP - 1]
        _request(_manifest_url(base), method="PUT", body=json.dumps(names).encode(), token=token)
        logger.info("Uploaded SQLite backup %s", name)
        return True
    except Exception:
        logger.exception("SQLite backup upload failed; ingestion continues")
        return False

def restore_latest_backup(database_path: Path) -> bool:
    """Restore the newest manifest entry, if configured and available."""
    base, token = _config()
    if not base:
        return False
    try:
        manifest = json.loads(_request(_manifest_url(base), token=token) or b"[]")
        names = [x for x in manifest if isinstance(x, str)]
        if not names:
            return False
        payload = _request(f"{base}/{names[0]}", token=token)
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            with gzip.GzipFile(fileobj=__import__("io").BytesIO(payload)) as source, open(temporary_path, "wb") as target:
                shutil.copyfileobj(source, target)
            connection = sqlite3.connect(temporary_path)
            try:
                connection.execute("PRAGMA integrity_check")
            finally:
                connection.close()
            database_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary_path, database_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        logger.info("Restored SQLite backup %s", names[0])
        return True
    except (urllib.error.URLError, OSError, ValueError, sqlite3.Error, gzip.BadGzipFile, json.JSONDecodeError):
        logger.warning("SQLite backup restore skipped: no usable backup available")
        return False


def restore_bundled_seed(database_path: Path) -> bool:
    """Populate empty database from bundled repository seed if present."""
    project_root = Path(__file__).resolve().parents[1]
    seed_gz = project_root / "data" / "seed_bhavcopy.sqlite3.gz"
    if not seed_gz.exists():
        return False
    try:
        database_path = Path(database_path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if database_path.exists():
            try:
                with sqlite3.connect(database_path) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM daily_equity_bars").fetchone()[0]
                    if count > 0:
                        return False
            except Exception:
                pass
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with gzip.open(seed_gz, "rb") as f_in, open(tmp_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            with sqlite3.connect(database_path) as dest:
                dest.execute(f"ATTACH DATABASE '{tmp_path.as_posix()}' AS seed")
                dest.execute("INSERT OR IGNORE INTO daily_equity_bars SELECT * FROM seed.daily_equity_bars")
                try:
                    dest.execute("INSERT OR IGNORE INTO daily_index_bars SELECT * FROM seed.daily_index_bars")
                except Exception:
                    pass
                dest.commit()
                dest.execute("DETACH DATABASE seed")
            logger.info("Restored bundled bhavcopy seed into %s", database_path.name)
            return True
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed to restore bundled bhavcopy seed")
        return False

