"""v0.13: versioned database upgrades, health check, second backup copy and the backup test."""
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from app import backup, check, migrations
from app.config import settings

PW = "lab-bench-pw-2026"
ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ upgrades

def _fresh_engine(tmp_path, name="t.db"):
    return create_engine(f"sqlite:///{tmp_path / name}")


def test_fresh_database_gets_every_step(client):
    from app.db import engine
    assert migrations.current_version(engine) == migrations.SCHEMA_VERSION
    names = {i["name"] for i in inspect(engine).get_indexes("experiment")}
    assert "ix_experiment_updated_at" in names


def test_old_database_without_version_table_is_upgraded(tmp_path):
    from app import models  # noqa: F401
    eng = _fresh_engine(tmp_path)
    SQLModel.metadata.create_all(eng)  # what a v0.12 database looks like: tables, no benchtop_schema
    assert migrations.current_version(eng) == 0
    ran = migrations.upgrade(eng)
    assert [m.number for m in ran] == list(range(1, migrations.SCHEMA_VERSION + 1))
    assert migrations.upgrade(eng) == []  # second run: nothing to do
    rows = migrations.applied(eng)
    assert all(r["app_version"] for r in rows) and len(rows) == migrations.SCHEMA_VERSION


def test_newer_database_is_refused_untouched(tmp_path):
    from app import models  # noqa: F401
    eng = _fresh_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    migrations.upgrade(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO benchtop_schema VALUES (999, 'from the future', '9.9.9', '2030-01-01')"))
    with pytest.raises(migrations.TooNew, match="9.9.9"):
        migrations.upgrade(eng)


def test_migration_numbers_are_consecutive():
    assert [m.number for m in migrations.MIGRATIONS] == list(range(1, len(migrations.MIGRATIONS) + 1))


# ------------------------------------------------------------------ health check

def _signed_record(client, users, notebook):
    h = users["alice"]
    e = client.post("/api/experiments", headers=h, json={"title": "check me", "notebook_id": notebook["id"]}).json()
    en = client.post(f"/api/experiments/{e['id']}/entries", headers=h, json={"kind": "text", "content": {"html": "<p>signed text</p>"}}).json()
    client.post(f"/api/experiments/{e['id']}/attachments", headers=h, files={"file": ("x.txt", b"hello file", "text/plain")})
    assert client.post(f"/api/experiments/{e['id']}/sign", headers=h, json={"password": PW}).status_code == 200
    return e, en


def _snapshot(tmp_path) -> Path:
    snap = tmp_path / "snap.db"
    src, dst = sqlite3.connect(backup._sqlite_path()), sqlite3.connect(snap)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return snap


def test_check_passes_on_good_data(client, users, notebook):
    _signed_record(client, users, notebook)
    res = check.check_live()
    assert res["ok"], check.report(res)
    assert [c["name"] for c in res["checks"]] == ["Database file", "Database version", "Audit trail", "Signed records", "Attached files"]


def test_check_catches_edited_signed_record_and_audit_and_files(client, users, notebook, tmp_path):
    e, en = _signed_record(client, users, notebook)
    snap = _snapshot(tmp_path)
    con = sqlite3.connect(snap)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("UPDATE entry SET content=? WHERE id=?", (json.dumps({"html": "<p>quietly changed</p>"}), en["id"]))
    con.execute("UPDATE auditevent SET action='edited' WHERE id=(SELECT MIN(id) FROM auditevent)")
    con.commit()
    con.close()
    att = tmp_path / "att"
    shutil.copytree(settings.attachments_dir, att)
    victim = next(p for p in att.rglob("*") if p.is_file())
    victim.write_bytes(b"tampered")
    res = check.check_files(snap, att)
    bad = {c["name"] for c in res["checks"] if not c["ok"]}
    assert {"Signed records", "Audit trail", "Attached files"} <= bad
    assert e["eid"] in next(c["detail"] for c in res["checks"] if c["name"] == "Signed records")


def test_check_rejects_garbage_file(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    res = check.check_files(junk, tmp_path)
    assert not res["ok"]


# ------------------------------------------------------------------ backups: fingerprint, second copy, test

@pytest.fixture()
def copy_folder(tmp_path):
    d = tmp_path / "offsite" / "Benchtop backups"
    (tmp_path / "offsite").mkdir()
    yield d
    backup.set_copy_dir(None)


def test_manifest_records_version_and_audit_fingerprint(client, admin):
    b = backup.create_backup("test")
    assert b["schema_version"] == migrations.SCHEMA_VERSION
    assert b["audit_head"] and len(b["audit_head"]) == 64 and b["audit_events"] > 0
    with zipfile.ZipFile(b["path"]) as z:
        assert json.loads(z.read("manifest.json"))["audit_head"] == b["audit_head"]


def test_second_copy_and_backup_test(client, admin, copy_folder, monkeypatch):
    monkeypatch.setattr(settings, "backup_interval_hours", 24.0)
    assert any("only on this computer" in w for w in backup.health()["warnings"])
    r = client.put("/api/admin/backups/copy-dir", headers=admin, json={"path": str(copy_folder)})
    assert r.status_code == 200, r.text
    assert copy_folder.is_dir()
    b = client.post("/api/admin/backups", headers=admin).json()
    assert b["copy"]["ok"] and (copy_folder / b["name"]).exists()
    t = client.post("/api/admin/backups/test", headers=admin).json()
    assert t["ok"] and t["where"] == "second copy", t
    info = client.get("/api/admin/backups", headers=admin).json()
    assert info["copy_dir"] == str(copy_folder.resolve()) and info["last_check"]["ok"] and not info["warnings"]


def test_copy_folder_problems_are_reported(client, admin, tmp_path, copy_folder, monkeypatch):
    monkeypatch.setattr(settings, "backup_interval_hours", 24.0)
    r = client.put("/api/admin/backups/copy-dir", headers=admin, json={"path": str(settings.data_dir / "inside")})
    assert r.status_code == 400 and "outside" in r.json()["detail"]
    r = client.put("/api/admin/backups/copy-dir", headers=admin, json={"path": str(tmp_path / "no" / "such" / "drive")})
    assert r.status_code == 400 and "plugged in" in r.json()["detail"]
    assert client.put("/api/admin/backups/copy-dir", headers=admin, json={"path": "relative/path"}).status_code == 400
    # drive unplugged after choosing it: the backup still works, the copy failure is reported
    client.put("/api/admin/backups/copy-dir", headers=admin, json={"path": str(copy_folder)})
    shutil.rmtree(copy_folder)
    b = client.post("/api/admin/backups", headers=admin).json()
    assert Path(b["path"]).exists() and not b["copy"]["ok"]
    warnings = client.get("/api/dashboard", headers=admin).json()["system"]["backup_warnings"]
    assert any("Copying the latest backup failed" in w for w in warnings)


def test_backup_warnings_only_for_admins(client, users, monkeypatch):
    monkeypatch.setattr(settings, "backup_interval_hours", 24.0)
    assert client.get("/api/dashboard", headers=users["alice"]).json()["system"]["backup_warnings"] == []


def test_backup_test_spots_a_damaged_backup(client, admin, tmp_path):
    b = backup.create_backup("test")
    bad = tmp_path / b["name"]
    with zipfile.ZipFile(b["path"]) as zin, zipfile.ZipFile(bad, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "benchtop.db":
                data = data[:1000] + bytes(x ^ 0xFF for x in data[1000:1064]) + data[1064:]
            zout.writestr(item, data)
    res = check.check_backup(bad)
    assert not res["ok"] and "checksum" in res["checks"][0]["detail"]


def test_restore_refuses_a_backup_from_a_newer_version(client, tmp_path):
    b = backup.create_backup("test")
    newer = tmp_path / "newer.zip"
    with zipfile.ZipFile(b["path"]) as zin, zipfile.ZipFile(newer, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "manifest.json":
                m = json.loads(data)
                m.update(schema_version=999, app_version="9.9.9")
                data = json.dumps(m).encode()
            zout.writestr(item, data)
    before = backup._sqlite_path().stat().st_mtime
    with pytest.raises(backup.BackupError, match="newer Benchtop"):
        backup.restore(str(newer))
    assert backup._sqlite_path().stat().st_mtime == before  # nothing moved aside or replaced


# ------------------------------------------------------------------ GitHub workflow

def test_github_workflow_matches_its_source():
    src = ROOT / "deploy" / "github-tests.yml"
    dst = ROOT / ".github" / "workflows" / "tests.yml"
    assert src.read_text() == dst.read_text(), "run: cp deploy/github-tests.yml .github/workflows/tests.yml"
