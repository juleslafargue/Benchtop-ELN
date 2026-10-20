"""0.18.2: the lab-server setup uses the standard database (SQLite), so its built-in backups actually run.

Regression: deploy/docker-compose.server.yml used Postgres, which the built-in backups skip, while
docs/SERVER.md said backups were written nightly. A server set up from the guide had none."""
from pathlib import Path

import pytest
import yaml

from app import backup, check
from app.config import settings
from app.db import MovedFromPostgres, check_not_moved_from_postgres

ROOT = Path(__file__).resolve().parents[2]


def compose(name: str) -> dict:
    return yaml.safe_load((ROOT / "deploy" / name).read_text())


def test_the_server_setup_uses_the_standard_database():
    c = compose("docker-compose.server.yml")
    app = c["services"]["app"]
    assert "db" not in c["services"], "no separate database server"
    assert "BENCHTOP_DATABASE_URL" not in app["environment"], "no other database: Benchtop's SQLite in /data"
    assert "BENCHTOP_DATA_DIR=/data" in (ROOT / "Dockerfile").read_text()
    vols = app["volumes"]
    assert "benchtop-data:/data" in vols  # the database and attached files, kept across upgrades
    assert app["environment"]["BENCHTOP_BACKUP_DIR"] == "/backups"
    assert any(v.endswith(":/backups") for v in vols) and any(v.endswith(":/backup-copy") for v in vols)
    assert app["environment"]["BENCHTOP_LEGACY_POSTGRES"] == "${POSTGRES_PASSWORD:-}"
    assert "POSTGRES_PASSWORD" not in (ROOT / "deploy" / "server.env.example").read_text()


def test_the_postgres_setup_says_its_backups_are_yours():
    head = (ROOT / "deploy" / "docker-compose.server-postgres.yml").read_text().split("services:")[0]
    assert "DO NOT RUN" in head and "pg_dump" in head
    assert "BENCHTOP_DATABASE_URL" in compose("docker-compose.server-postgres.yml")["services"]["app"]["environment"]


def test_with_the_server_settings_the_daily_backup_runs_copies_and_restores(client, tmp_path, monkeypatch):
    """What the server's housekeeping does: the scheduled backup (which used to skip silently), the second copy,
    and the weekly restore test."""
    monkeypatch.setattr(settings, "backup_dir", tmp_path / "backups")
    monkeypatch.setattr(settings, "backup_interval_hours", 24.0)
    copy = tmp_path / "backup-copy"
    copy.mkdir()
    backup.set_copy_dir(str(copy))
    try:
        made = backup.maybe_backup()
        assert made is not None, "the scheduled backup didn't run"
        assert made["copy"]["ok"] and (copy / made["name"]).exists()
        tested = backup.test_latest_backup()
        assert tested["ok"] and tested["where"] == "second copy", tested
        assert check.check_backup(copy / made["name"])["ok"]
        assert not any("can't run" in w for w in backup.health()["warnings"])
    finally:
        backup.set_copy_dir(None)


def test_another_database_gets_a_clear_warning_not_silence(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://benchtop:x@db:5432/benchtop")
    monkeypatch.setattr(settings, "backup_interval_hours", 24.0)
    assert backup.maybe_backup() is None
    warnings = backup.health()["warnings"]
    assert len(warnings) == 1 and "can't run" in warnings[0] and "pg_dump" in warnings[0]


def test_a_server_that_used_postgres_wont_start_on_an_empty_database(tmp_path, monkeypatch):
    db = tmp_path / "benchtop.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db}")
    monkeypatch.setattr(settings, "legacy_postgres", True)
    with pytest.raises(MovedFromPostgres, match="still in Postgres"):
        check_not_moved_from_postgres()
    assert not db.exists(), "nothing was created"
    db.write_bytes(b"")  # moved to SQLite on purpose earlier: fine
    check_not_moved_from_postgres()
    db.unlink()
    monkeypatch.setattr(settings, "legacy_postgres", False)  # a new server: fine
    check_not_moved_from_postgres()
    monkeypatch.setattr(settings, "legacy_postgres", True)
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://benchtop:x@db:5432/benchtop")
    check_not_moved_from_postgres()  # still on Postgres: fine
