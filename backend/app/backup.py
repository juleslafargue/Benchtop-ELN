"""Backups of the database and attachments.

A backup is a single zip: a consistent SQLite snapshot (taken with SQLite's online backup
API, safe while the server is running), every attachment blob, and a manifest. The server
makes one on startup if the newest is older than the interval, then checks hourly.

CLI (run from backend/):
    python -m app.backup create
    python -m app.backup list
    python -m app.backup test                   # unpack the newest backup and check it
    python -m app.backup restore <file.zip>     # stop the server first
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
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


_lock = threading.Lock()  # one backup at a time in this server (two clicks, or a click during the scheduled one)


def create_backup(reason: str = "manual") -> dict[str, Any]:
    with _lock:
        return _create_backup(reason)


def _create_backup(reason: str) -> dict[str, Any]:
    db = _sqlite_path()
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final = settings.backup_dir / f"{PREFIX}{stamp}.zip"
    unique = f"{stamp}-{os.getpid()}-{threading.get_ident()}"  # also safe beside a ./benchtop backup in another process
    tmp_db = settings.backup_dir / f".snapshot-{unique}.db"
    tmp_zip = settings.backup_dir / f".{PREFIX}{unique}.zip.part"
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
            **_fingerprint(tmp_db),
        }
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(tmp_db, "benchtop.db")
            for b in blobs:  # attachments are often already compressed; store them as-is
                z.write(b, f"attachments/{b.relative_to(settings.attachments_dir)}", compress_type=zipfile.ZIP_STORED)
            plug = settings.data_dir / "plugins"
            for f in sorted(plug.glob("*.toml")) if plug.is_dir() else []:  # your lab's plugin files
                z.write(f, f"plugins/{f.name}")
        final = _claim_name(tmp_zip, final)
    finally:
        tmp_db.unlink(missing_ok=True)
        tmp_zip.unlink(missing_ok=True)
    prune()
    out = {"name": final.name, "path": str(final), "size": final.stat().st_size, **manifest}
    out["copy"] = copy_to_second_place(final)
    return out


def _fingerprint(db: Path) -> dict[str, Any]:
    """Database version and the audit trail's latest fingerprint, recorded in the manifest. Kept in a
    second place, these make it detectable if the trail were ever rewritten wholesale later."""
    try:
        con = sqlite3.connect(db)
        try:
            head = con.execute("SELECT hash FROM auditevent ORDER BY id DESC LIMIT 1").fetchone()
            n = con.execute("SELECT COUNT(*) FROM auditevent").fetchone()[0]
            try:
                v = con.execute("SELECT MAX(number) FROM benchtop_schema").fetchone()[0] or 0
            except sqlite3.OperationalError:
                v = 0
        finally:
            con.close()
        return {"schema_version": v, "audit_events": n, "audit_head": head[0] if head else None}
    except sqlite3.Error:
        return {}


# ------------------------------------------------------------------ a second copy, somewhere else

def _state_path() -> Path:
    return settings.data_dir / "backup-settings.json"


def load_state() -> dict[str, Any]:
    """Second-copy folder and the latest copy/check results (kept outside the database on purpose)."""
    try:
        return json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_state(**changes: Any) -> dict[str, Any]:
    st = {**load_state(), **changes}
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(_state_path())
    return st


def copy_dir() -> Path | None:
    env = os.environ.get("BENCHTOP_BACKUP_COPY_DIR")
    raw = env if env is not None else load_state().get("copy_dir")
    return Path(raw).expanduser() if raw else None


def validate_copy_dir(raw: str) -> Path:
    """A folder that exists (or whose parent does), is writable, and isn't inside Benchtop's own data."""
    try:
        return _validate_copy_dir(raw)
    except OSError as exc:  # a name too long, no permission, a drive that vanished…
        raise BackupError(f"Benchtop can't use that folder: {exc.strerror or exc}") from None


def _validate_copy_dir(raw: str) -> Path:
    if len(raw) > 1024:
        raise BackupError("That path is too long")
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        raise BackupError("Give the full path of the folder, e.g. /Volumes/MyDrive/Benchtop backups")
    p = p.resolve()
    data = settings.data_dir.resolve()
    if p == data or data in p.parents or p == settings.backup_dir.resolve():
        raise BackupError("Choose a folder outside Benchtop's data folder; the point is a copy somewhere else")
    if not p.exists():
        if not p.parent.is_dir():
            raise BackupError(f"{p.parent} doesn't exist. Is the drive plugged in?")
        p.mkdir()
    if not p.is_dir():
        raise BackupError(f"{p} is a file, not a folder")
    probe = p / ".benchtop-write-test"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        raise BackupError(f"Benchtop can't write to {p}: {exc.strerror or exc}") from None
    return p


def set_copy_dir(raw: str | None) -> dict[str, Any]:
    if not raw:
        return _save_state(copy_dir=None, last_copy=None)
    p = validate_copy_dir(raw)
    return _save_state(copy_dir=str(p), last_copy=None)


def suggested_copy_dirs() -> list[dict[str, str]]:
    """Places a Mac usually has for an off-laptop copy: iCloud Drive and plugged-in drives."""
    out = []
    icloud = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if icloud.is_dir():
        out.append({"label": "iCloud Drive", "path": str(icloud / "Benchtop backups")})
    vols = Path("/Volumes")
    if vols.is_dir():
        for v in sorted(vols.iterdir()):
            if v.is_dir() and not v.is_symlink() and v.name not in ("Macintosh HD", "Recovery", "Preboot", "VM", "Data"):
                out.append({"label": f"Drive “{v.name}”", "path": str(v / "Benchtop backups")})
    for name in ("Dropbox", "Google Drive", "OneDrive"):
        d = Path.home() / name
        if d.is_dir():
            out.append({"label": name, "path": str(d / "Benchtop backups")})
    return out


def copy_backups() -> list[Path]:
    d = copy_dir()
    return sorted(d.glob(f"{PREFIX}*.zip"), reverse=True) if d and d.is_dir() else []


def copy_to_second_place(src: Path) -> dict[str, Any] | None:
    """Copy a finished backup to the second folder (keeping the same number as locally)."""
    d = copy_dir()
    if d is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    try:
        if not d.is_dir():
            raise BackupError(f"{d} isn't there. If it's on an external drive, plug it in; the next backup will be copied.")
        part = d / f".{src.name}.{os.getpid()}-{threading.get_ident()}.part"
        shutil.copyfile(src, part)
        if hashlib.sha256(part.read_bytes()).hexdigest() != hashlib.sha256(src.read_bytes()).hexdigest():
            part.unlink(missing_ok=True)
            raise BackupError("The copy didn't match the original; it was removed")
        part.replace(d / src.name)
        for old in copy_backups()[settings.backup_keep:]:
            old.unlink(missing_ok=True)
        res = {"ok": True, "at": now, "name": src.name, "folder": str(d)}
    except (OSError, BackupError) as exc:
        res = {"ok": False, "at": now, "name": src.name, "folder": str(d), "error": str(exc)}
    _save_state(last_copy=res)
    return res


# ------------------------------------------------------------------ the weekly backup test

CHECK_EVERY_DAYS = 7


def test_latest_backup() -> dict[str, Any] | None:
    """Unpack the newest backup (the second copy when there is one: that's what you'd restore from if
    this computer were lost) and check it as if it had been restored."""
    from .check import check_backup

    copies = copy_backups()
    target = copies[0] if copies else (Path(list_backups()[0]["path"]) if list_backups() else None)
    if target is None:
        return None
    res = check_backup(target)
    summary = {"ok": res["ok"], "at": datetime.now(timezone.utc).isoformat(), "name": target.name,
               "where": "second copy" if copies else "this computer", "checks": res["checks"], "counts": res.get("counts", {})}
    _save_state(last_check=summary)
    return summary


def maybe_test_backup() -> dict[str, Any] | None:
    if settings.backup_interval_hours <= 0:
        return None
    last = load_state().get("last_check") or {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last["at"])).total_seconds() / 86400 if last else None
    except (KeyError, ValueError):
        age = None
    if age is not None and age < CHECK_EVERY_DAYS and last.get("ok"):
        return None
    if age is not None and age < 1:  # a failed test is retried daily, not hourly
        return None
    return test_latest_backup()


def health() -> dict[str, Any]:
    """What the admin page and dashboard show about backups."""
    st = load_state()
    b = list_backups()
    newest = b[0] if b else None
    age_h = newest_age_hours()
    warnings = []
    if settings.backup_interval_hours > 0:
        if newest is None:
            warnings.append("No backups yet.")
        elif age_h is not None and age_h > max(72, settings.backup_interval_hours * 3):
            warnings.append(f"The newest backup is {age_h / 24:.0f} days old.")
        if copy_dir() is None:
            warnings.append("Backups are only on this computer. Choose a second place (iCloud Drive or an external drive) in Admin → Backups.")
        elif st.get("last_copy") and not st["last_copy"].get("ok"):
            warnings.append(f"Copying the latest backup failed: {st['last_copy'].get('error')}")
        if st.get("last_check") and not st["last_check"].get("ok"):
            bad = [c["name"] for c in st["last_check"].get("checks", []) if not c["ok"]]
            warnings.append(f"The latest backup test found problems ({', '.join(bad) or 'see Admin → Backups'}).")
    return {"copy_dir": str(copy_dir()) if copy_dir() else None, "copy_dir_from_env": "BENCHTOP_BACKUP_COPY_DIR" in os.environ,
            "last_copy": st.get("last_copy"), "last_check": st.get("last_check"), "copies": len(copy_backups()),
            "warnings": warnings}


def _claim_name(tmp: Path, final: Path) -> Path:
    """Give the finished zip its name without ever replacing another backup (two in the same second get -2, -3…)."""
    n = 1
    while True:
        target = final if n == 1 else final.with_name(f"{final.stem}-{n}{final.suffix}")
        try:
            os.link(tmp, target)  # fails if the name is taken, unlike rename
        except FileExistsError:
            n += 1
            continue
        except OSError:  # file systems without hard links: fall back to a checked rename
            if target.exists():
                n += 1
                continue
            tmp.rename(target)
            return target
        tmp.unlink(missing_ok=True)
        return target


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
        from .migrations import SCHEMA_VERSION
        if (manifest.get("schema_version") or 0) > SCHEMA_VERSION:
            raise BackupError(f"This backup was made by a newer Benchtop ({manifest.get('app_version')}); install that version "
                              "or newer first, then restore it. Nothing was changed.")
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
        elif cmd == "test":
            from .check import report
            r = test_latest_backup()
            print("No backups to test yet." if r is None else f"Tested {r['name']} ({r['where']}):\n" + report(r))
            return 0 if r is None or r["ok"] else 1
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
