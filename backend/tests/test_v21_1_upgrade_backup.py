"""0.21.1: Benchtop backs up by itself before it upgrades the database.

`./benchtop update` always backed up first, but code that arrived another way (merged in GitHub Desktop, git pull, a
copied folder) upgraded the database on the next start with no backup from just before, so going back to the
previous version could lose the latest work. Now the start-up itself backs up when a database step is due:
- the backup keeps the old database version (the previous Benchtop can restore it), and says why it was made
- not on a new, empty database; not when nothing is due; not twice right after ./benchtop update's own backup
- if the backup fails, nothing is upgraded and the message says why
- BENCHTOP_BACKUP_INTERVAL_HOURS=0 (automatic backups off) leaves it out too
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

from app import migrations

BACKEND = Path(__file__).resolve().parents[1]


def start(data: Path, **env: str) -> subprocess.CompletedProcess:
    """What starting Benchtop does to the database, in its own process (as `./benchtop` would)."""
    e = {**os.environ, "BENCHTOP_DATA_DIR": str(data), "BENCHTOP_BACKUP_INTERVAL_HOURS": "24", **env}
    e.pop("BENCHTOP_BACKUP_DIR", None)
    e.update(env)
    return subprocess.run([sys.executable, "-c", "from app.db import init_db; init_db()"], cwd=BACKEND, env=e,
                          capture_output=True, text=True, timeout=120)


def version(data: Path) -> int:
    with sqlite3.connect(data / "benchtop.db") as c:
        return c.execute("SELECT MAX(number) FROM benchtop_schema").fetchone()[0]


def as_previous_version(data: Path) -> None:
    """The database as the previous Benchtop left it: the newest step not yet run, and some work in it."""
    with sqlite3.connect(data / "benchtop.db") as c:
        c.execute("DELETE FROM benchtop_schema WHERE number = ?", (migrations.SCHEMA_VERSION,))
        c.execute("CREATE TABLE IF NOT EXISTS my_work (x TEXT)")
        c.execute("INSERT INTO my_work VALUES ('yield 87%')")


def backups(data: Path) -> list[Path]:
    return sorted((data / "backups").glob("benchtop-backup-*.zip"))


def manifest(z: Path) -> dict:
    with zipfile.ZipFile(z) as f:
        return json.loads(f.read("manifest.json"))


def test_start_backs_up_before_a_database_step_and_the_backup_keeps_the_old_version(tmp_path):
    data = tmp_path / "data"
    r = start(data)
    assert r.returncode == 0, r.stderr
    assert backups(data) == [] and version(data) == migrations.SCHEMA_VERSION  # new, empty database: nothing to keep

    as_previous_version(data)
    r = start(data)
    assert r.returncode == 0, r.stderr
    assert len(backups(data)) == 1, r.stderr
    m = manifest(backups(data)[0])
    assert m["reason"] == "before-upgrade" and m["schema_version"] == migrations.SCHEMA_VERSION - 1
    assert "Backed up your data before upgrading the database" in r.stderr and str(backups(data)[0]) in r.stderr
    assert version(data) == migrations.SCHEMA_VERSION  # and then upgraded
    with zipfile.ZipFile(backups(data)[0]) as z:  # the work is in the backup
        snap = tmp_path / "snap.db"
        snap.write_bytes(z.read("benchtop.db"))
    with sqlite3.connect(snap) as c:
        assert c.execute("SELECT x FROM my_work").fetchall() == [("yield 87%",)]

    r = start(data)  # nothing due: no backup
    assert r.returncode == 0 and len(backups(data)) == 1 and "Backed up" not in r.stderr


def test_no_second_backup_right_after_benchtop_updates_own(tmp_path):
    data = tmp_path / "data"
    start(data)
    as_previous_version(data)
    made = subprocess.run([sys.executable, "-m", "app.backup", "create"], cwd=BACKEND, capture_output=True, text=True,
                          env={**os.environ, "BENCHTOP_DATA_DIR": str(data)})  # what ./benchtop update does first
    assert made.returncode == 0, made.stderr
    r = start(data)
    assert r.returncode == 0, r.stderr
    assert len(backups(data)) == 1 and version(data) == migrations.SCHEMA_VERSION


def test_a_failed_backup_means_no_upgrade(tmp_path):
    data = tmp_path / "data"
    start(data)
    as_previous_version(data)
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("a file where the backups folder should be")
    r = start(data, BENCHTOP_BACKUP_DIR=str(blocked))
    assert r.returncode != 0
    assert "backs up your data first, but the backup didn't work" in r.stderr and "Nothing was changed" in r.stderr
    assert version(data) == migrations.SCHEMA_VERSION - 1  # left as it was


def test_automatic_backups_off_means_none_here_either(tmp_path):
    data = tmp_path / "data"
    start(data)
    as_previous_version(data)
    r = start(data, BENCHTOP_BACKUP_INTERVAL_HOURS="0")
    assert r.returncode == 0, r.stderr
    assert backups(data) == [] and version(data) == migrations.SCHEMA_VERSION
