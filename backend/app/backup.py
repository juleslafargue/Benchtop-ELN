"""Backups of the database and attachments.

A backup is a single zip: a consistent SQLite snapshot (taken with SQLite's online backup
API, safe while the server is running), every attachment blob, and a manifest. The server
makes one on startup if the newest is older than the interval, then checks hourly.

CLI (run from backend/):
    python -m app.backup create
    python -m app.backup list
    python -m app.backup restore <file.zip>     # stop the server first
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import settings

PREFIX = "benchtop-backup-"


class BackupError(RuntimeError):
    pass


def _sqlite_path() -> Path:
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise BackupError("Built-in backups cover SQLite only; for Postgres use pg_dump on a schedule.")
    return Path(url.removeprefix("sqlite:///"))


def list_backups() -> list[dict[str, Any]]:
    if not settings.backup_dir.exists():
        return []
    out = []
    for p in sorted(settings.backup_dir.glob(f"{PREFIX}*.zip"), reverse=True):
        st = p.stat()
        out.append({"name": p.name, "path": str(p), "size": st.st_size,
                    "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return out


def newest_age_hours() -> float | None:
    b = list_backups()
    if not b:
        return None
    return (time.time() - Path(b[0]["path"]).stat().st_mtime) / 3600


def create_backup(reason: str = "manual") -> dict[str, Any]:
    db = _sqlite_path()
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final = settings.backup_dir / f"{PREFIX}{stamp}.zip"
    tmp_db = settings.backup_dir / f".snapshot-{stamp}.db"
    tmp_zip = final.with_suffix(".zip.part")
    try:
        src = sqlite3.connect(db)
        dst = sqlite3.connect(tmp_db)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        blobs = [p for p in settings.attachments_dir.rglob("*") if p.is_file()]
        manifest = {
            "app_version": __version__, "created_at": datetime.now(timezone.utc).isoformat(), "reason": reason,
            "database_sha256": hashlib.sha256(tmp_db.read_bytes()).hexdigest(), "attachments": len(blobs),
        }
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(tmp_db, "benchtop.db")
            for b in blobs:  # attachments are often already compressed; store them as-is
                z.write(b, f"attachments/{b.relative_to(settings.attachments_dir)}", compress_type=zipfile.ZIP_STORED)
            plug = settings.data_dir / "plugins"
            for f in sorted(plug.glob("*.toml")) if plug.is_dir() else []:  # your lab's plugin files
                z.write(f, f"plugins/{f.name}")
        tmp_zip.rename(final)
    finally:
        tmp_db.unlink(missing_ok=True)
        tmp_zip.unlink(missing_ok=True)
    prune()
    return {"name": final.name, "path": str(final), "size": final.stat().st_size, **manifest}


def prune(keep: int | None = None) -> None:
    keep = settings.backup_keep if keep is None else keep
    for b in list_backups()[keep:]:
        Path(b["path"]).unlink(missing_ok=True)


def restore(zip_path: str) -> Path:
    """Replace the current database and attachments with a backup's.
    The current data is moved aside first, never deleted."""
    zp = Path(zip_path).expanduser().resolve()
    if not zp.exists():
        raise BackupError(f"{zp} not found")
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        if "benchtop.db" not in names or "manifest.json" not in names:
            raise BackupError("Not a Benchtop backup (missing benchtop.db or manifest.json)")
        manifest = json.loads(z.read("manifest.json"))
        db_bytes = z.read("benchtop.db")
        if hashlib.sha256(db_bytes).hexdigest() != manifest["database_sha256"]:
            raise BackupError("Backup is corrupt: database checksum does not match")
        db = _sqlite_path()
        from .db import engine

        engine.dispose()  # release pooled connections to the file being replaced
        aside = settings.data_dir / f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}"
        n = 1
        while aside.exists():  # two restores within the same second
            n += 1
            aside = aside.with_name(f"{aside.name.split('~')[0]}~{n}")
        aside.mkdir(parents=True)
        for p in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
            if p.exists():
                shutil.move(str(p), aside / p.name)
        if settings.attachments_dir.exists():
            shutil.move(str(settings.attachments_dir), aside / "attachments")
        settings.attachments_dir.mkdir(parents=True)
        db.write_bytes(db_bytes)
        for n in names:
            if n.startswith("attachments/") and not n.endswith("/"):
                target = (settings.attachments_dir / n.removeprefix("attachments/")).resolve()
                if settings.attachments_dir.resolve() not in target.parents:
                    raise BackupError(f"Refusing unsafe path in backup: {n}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(n))
        plug_names = [n for n in names if n.startswith("plugins/") and n.endswith(".toml") and "/" not in n.removeprefix("plugins/")]
        if plug_names:  # backups from v0.10 on also hold the lab's plugin files
            plug = settings.data_dir / "plugins"
            if plug.exists():
                shutil.move(str(plug), aside / "plugins")
            plug.mkdir(parents=True)
            for n in plug_names:
                (plug / Path(n).name).write_bytes(z.read(n))
    engine.dispose()
    return aside


def maybe_backup() -> dict[str, Any] | None:
    """Make a backup if none exists or the newest is older than the interval."""
    if settings.backup_interval_hours <= 0:
        return None
    try:
        _sqlite_path()
    except BackupError:
        return None
    age = newest_age_hours()
    if age is None or age >= settings.backup_interval_hours:
        return create_backup("scheduled")
    return None


def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "help"
    try:
        if cmd == "create":
            b = create_backup("manual")
            print(f"Backup written: {b['path']} ({b['size'] / 1e6:.1f} MB, {b['attachments']} attachments)")
        elif cmd == "list":
            bs = list_backups()
            print(f"Backups in {settings.backup_dir}:" if bs else f"No backups in {settings.backup_dir}")
            for b in bs:
                print(f"  {b['name']}  {b['size'] / 1e6:6.1f} MB")
        elif cmd == "restore" and len(argv) > 2:
            aside = restore(argv[2])
            print(f"Restored {argv[2]}.\nYour previous data was kept in {aside}")
        else:
            print(__doc__)
            return 1
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
