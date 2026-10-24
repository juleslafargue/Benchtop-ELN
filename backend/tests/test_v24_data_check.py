"""0.24: Benchtop checks its live data once a day and tells the admins what's wrong, and what to do.

- tampering is found by the next check and shown on the admin's dashboard: an attached file changed or deleted, a
  signed section's content edited directly in the database, an audit event altered (the chain breaks)
- a passing check shows nothing alarming; Admin → Backups says when it last ran
- it doesn't run again within the day; one that found problems is tried again hours later, not every hour
- BENCHTOP_CHECK_INTERVAL_HOURS=0 turns it off; on Postgres it doesn't run, and says so instead of "all good"
- a big lab re-reads a seventh of its files a day (each once a week), but a missing or resized file is found at once
- admins get one email per new problem, not one a day
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import check, integrity, mailer, sitesettings
from app.backup import _sqlite_path
from app.config import settings

PW = "lab-bench-pw-2026"
T0 = datetime(2030, 3, 4, 3, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(settings, "check_interval_hours", 24.0)
    integrity.forget()
    yield
    integrity.forget()


@pytest.fixture(scope="module")
def record(client, users):
    """A signed record with an attached file."""
    h = users["alice"]
    nb = client.post("/api/notebooks", headers=h, json={"name": "Data check"}).json()
    exp = client.post("/api/experiments", headers=h, json={"title": "Checked every day", "notebook_id": nb["id"]}).json()
    entry = client.post(f"/api/experiments/{exp['id']}/entries", headers=h,
                        json={"kind": "text", "title": "Result", "content": {"html": "<p>Yield 87%.</p>"}}).json()
    att = client.post(f"/api/experiments/{exp['id']}/attachments", headers=h,
                      files={"file": ("hplc.csv", b"time,mAU\n0.0,1.2\n0.1,1.4\n", "text/csv")}).json()
    r = client.post(f"/api/experiments/{exp['id']}/sign", headers=h, json={"password": PW})
    assert r.status_code == 200, r.text
    return {"exp": exp, "entry": entry, "att": att, "file": settings.attachments_dir / att["sha256"][:2] / att["sha256"]}


def db() -> sqlite3.Connection:
    return sqlite3.connect(_sqlite_path())


def warnings(client, admin) -> list[str]:
    return client.get("/api/dashboard", headers=admin).json()["system"]["check_warnings"]


def failed(res) -> list[str]:
    return [c["name"] for c in res["checks"] if not c["ok"]]


# ------------------------------------------------------------------ tampering is found, and shown with what to do

def _change_file(rec):
    data = rec["file"].read_bytes()
    rec["file"].write_bytes(data + b"0.2,9.9\n")
    return lambda: rec["file"].write_bytes(data)


def _delete_file(rec):
    data = rec["file"].read_bytes()
    rec["file"].unlink()
    return lambda: rec["file"].write_bytes(data)


def _edit_signed_section(rec):
    with db() as c:
        raw = c.execute("SELECT content FROM entry WHERE id = ?", (rec["entry"]["id"],)).fetchone()[0]
        c.execute("UPDATE entry SET content = ? WHERE id = ?", (raw.replace("87%", "97%"), rec["entry"]["id"]))

    def undo():
        with db() as c:
            c.execute("UPDATE entry SET content = ? WHERE id = ?", (raw, rec["entry"]["id"]))
    return undo


def _alter_audit_event(rec):
    with db() as c:
        eid, raw = c.execute("SELECT id, details FROM auditevent WHERE action = 'sign:author' ORDER BY id DESC LIMIT 1").fetchone()
        c.execute("UPDATE auditevent SET details = ? WHERE id = ?", (raw.replace('"comment"', '"note"'), eid))

    def undo():
        with db() as c:
            c.execute("UPDATE auditevent SET details = ? WHERE id = ?", (raw, eid))
    return undo


@pytest.mark.parametrize("tamper, found, says", [
    (_change_file, "Attached files", "Attached files: 1 changed (hplc.csv). Backups keep every attached file unchanged: use Put back from backups"),
    (_delete_file, "Attached files", "Attached files: 1 missing (hplc.csv)."),
    (_edit_signed_section, "Signed records", "They were changed outside Benchtop. Open each one and use Verify integrity"),
    (_alter_audit_event, "Audit trail", "Audit trail: broken at event"),
])
def test_the_next_check_finds_tampering_and_the_dashboard_says_what_to_do(client, admin, users, record, tamper, found, says):
    assert integrity.run(T0)["ok"] and warnings(client, admin) == []
    undo = tamper(record)
    try:
        res = integrity.run(T0 + timedelta(days=1))
        assert not res["ok"] and failed(res) == [found], res["checks"]
        shown = warnings(client, admin)
        assert len(shown) == 1 and says in shown[0], shown
        if found == "Signed records":
            assert record["exp"]["eid"] in shown[0]
        assert warnings(client, users["alice"]) == []  # admins only
        info = client.get("/api/admin/backups", headers=admin).json()["data_check"]
        assert info["last"]["ok"] is False and info["warnings"] == shown
    finally:
        undo()
    assert integrity.run(T0 + timedelta(days=2))["ok"] and warnings(client, admin) == []


def test_a_passing_check_shows_nothing_alarming_and_when_it_ran(client, admin, record):
    res = integrity.run(T0)
    assert res["ok"] and res["every_file"] and res["counts"]["attachments"] >= 1
    assert {c["name"] for c in res["checks"]} >= {"Database file", "Audit trail", "Signed records", "Attached files"}
    assert warnings(client, admin) == []
    info = client.get("/api/admin/backups", headers=admin).json()["data_check"]
    assert info["enabled"] and info["available"] and info["last"]["at"] == T0.isoformat() and info["warnings"] == []


# ------------------------------------------------------------------ putting files back from the backups

def test_put_back_from_backups_copies_only_the_damaged_files_back(client, admin, users, record):
    from app import backup
    backup.create_backup("manual")  # keeps every attached file, unchanged, in benchtop-files
    good = record["file"].read_bytes()
    record["file"].write_bytes(good + b"0.2,9.9\n")
    with db() as c:
        before = c.execute("SELECT COUNT(*), MAX(id) FROM auditevent").fetchone()
    assert not integrity.run(T0)["ok"]
    assert client.post("/api/admin/check/put-back-files", headers=users["alice"]).status_code == 403
    r = client.post("/api/admin/check/put-back-files", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["put_back"] == ["hplc.csv"] and body["no_good_copy"] == [] and body["last"]["ok"] and body["warnings"] == []
    assert record["file"].read_bytes() == good
    aside = list((settings.data_dir / "damaged-files").glob(record["att"]["sha256"] + "-*"))
    assert aside and aside[-1].read_bytes() == good + b"0.2,9.9\n"  # the damaged one is kept, not deleted
    events = client.get("/api/audit", headers=admin).json()
    assert any(e["action"] == "put_back_files" for e in events)

    record["file"].unlink()  # gone altogether: put back too
    assert integrity.put_back_files()["put_back"] == ["hplc.csv"] and record["file"].read_bytes() == good
    assert integrity.put_back_files() == {"put_back": [], "no_good_copy": []}  # nothing to do


def test_a_file_with_no_good_copy_in_the_backups_is_named(client, users, record, tmp_path, monkeypatch):
    h = users["alice"]
    exp = client.post("/api/experiments", headers=h, json={"title": "Not backed up yet", "notebook_id": record["exp"]["notebook_id"]}).json()
    att = client.post(f"/api/experiments/{exp['id']}/attachments", headers=h, files={"file": ("new.csv", b"made after the backup\n", "text/csv")}).json()
    f = settings.attachments_dir / att["sha256"][:2] / att["sha256"]
    data = f.read_bytes()
    monkeypatch.setattr(settings, "backup_dir", tmp_path / "no-backups-here")
    f.unlink()
    try:
        assert integrity.put_back_files() == {"put_back": [], "no_good_copy": ["new.csv"]}
    finally:
        f.write_bytes(data)


# ------------------------------------------------------------------ when it runs

def test_once_a_day_and_a_failed_check_is_retried_hours_later_not_hourly(monkeypatch, record):
    assert integrity.maybe_run(T0)["ok"]
    for h in (1, 12, 23.5):
        assert integrity.maybe_run(T0 + timedelta(hours=h)) is None, h
    assert integrity.maybe_run(T0 + timedelta(hours=24.5))["ok"]

    def broken(rehash=None):
        raise OSError("disk I/O error")
    monkeypatch.setattr(check, "check_live", broken)
    t1 = T0 + timedelta(days=3)
    res = integrity.maybe_run(t1)
    assert not res["ok"] and "disk I/O error" in integrity.warnings(res)[0] and "couldn't run" in integrity.warnings(res)[0]
    for h in (1, 2, 5):
        assert integrity.maybe_run(t1 + timedelta(hours=h)) is None, h
    monkeypatch.undo()
    monkeypatch.setattr(settings, "check_interval_hours", 24.0)
    assert integrity.maybe_run(t1 + timedelta(hours=integrity.RETRY_HOURS + 0.5))["ok"]


def test_turned_off_by_its_setting(monkeypatch, client, admin):
    monkeypatch.setattr(settings, "check_interval_hours", 0.0)
    assert integrity.maybe_run(T0) is None and not integrity._path().exists()
    integrity._save_state(last={"ok": False, "at": T0.isoformat(), "checks": [{"name": "Audit trail", "ok": False, "detail": "x"}]})
    assert warnings(client, admin) == []  # an old result isn't shown while it's off
    assert client.get("/api/admin/backups", headers=admin).json()["data_check"]["enabled"] is False


def test_postgres_says_it_doesnt_run_instead_of_all_good(monkeypatch, client, admin):
    monkeypatch.setattr(settings, "database_url", "postgresql://benchtop@db/benchtop")
    assert integrity.maybe_run(T0) is None
    h = integrity.health()
    assert not h["available"] and "Postgres" in h["note"] and h["last"] is None and h["warnings"] == []
    r = client.post("/api/admin/check", headers=admin)
    assert r.status_code == 409 and "Postgres" in r.json()["detail"]


def test_check_now_is_for_admins(client, admin, users, record):
    for who in ("alice", "vic"):
        assert client.post("/api/admin/check", headers=users[who]).status_code == 403
    r = client.post("/api/admin/check", headers=admin)
    assert r.status_code == 200 and r.json()["last"]["ok"] and r.json()["warnings"] == []


# ------------------------------------------------------------------ a big lab

def test_a_big_lab_rereads_a_seventh_a_day_but_finds_a_missing_or_resized_file_at_once(monkeypatch, record):
    monkeypatch.setattr(integrity, "FULL_CHECK_BYTES", 0)  # as if the files totalled more than 20 GB
    sha = record["att"]["sha256"]
    its_day = next(T0 + timedelta(days=d) for d in range(7) if integrity.todays_share(T0 + timedelta(days=d))(sha))
    other_day = its_day + timedelta(days=1)
    shares = [sum(integrity.todays_share(T0 + timedelta(days=d))(f"{i:08x}" + "0" * 56) for d in range(7)) for i in range(0, 7000, 7)]
    assert set(shares) == {1}  # every file comes up on exactly one day of the week

    data = record["file"].read_bytes()
    record["file"].write_bytes(data[:-2] + b"9\n")  # changed in place, same size (bit rot)
    try:
        res = integrity.run(other_day)
        assert res["ok"] and not res["every_file"] and "re-read once a week" in res["checks"][-1]["detail"]
        res = integrity.run(its_day)
        assert failed(res) == ["Attached files"] and "1 changed (hplc.csv)" in res["checks"][-1]["detail"]
        record["file"].write_bytes(data + b"x")  # a different size is found any day
        assert failed(integrity.run(other_day)) == ["Attached files"]
    finally:
        record["file"].write_bytes(data)
    assert integrity.run(other_day)["ok"]


# ------------------------------------------------------------------ email

@pytest.fixture()
def outbox(monkeypatch, client, admin):
    sent = []
    monkeypatch.setattr(mailer, "synchronous", True)
    monkeypatch.setattr(mailer, "transport", lambda msg, cfg: sent.append(msg))
    sitesettings.update("email", {"host": "smtp.example.org", "from_address": "eln@example.org", "password": "s3cret-app-pw"})
    client.patch("/api/auth/me", headers=admin, json={"email": "admin@example.org"})
    yield sent
    client.patch("/api/auth/me", headers=admin, json={"email": ""})
    sitesettings.update("email", {"host": "", "from_address": ""})


def test_admins_get_one_email_per_new_problem(monkeypatch, outbox, record):
    undo = _change_file(record)
    try:
        integrity.run(T0)
        assert len(outbox) == 1 and outbox[0]["To"] == "admin@example.org"
        assert outbox[0]["Subject"] == "Benchtop's daily data check found a problem"
        body = outbox[0].get_body(("plain",)).get_content()
        assert "Attached files: 1 changed (hplc.csv)" in body and "Put back from backups" in body and "/admin" in body
        integrity.run(T0 + timedelta(days=1))
        assert len(outbox) == 1, "the same problem again: no second email"
    finally:
        undo()
    integrity.run(T0 + timedelta(days=2))
    assert len(outbox) == 1  # all good: nothing to say
    undo = _delete_file(record)
    try:
        integrity.run(T0 + timedelta(days=3))
        assert len(outbox) == 2  # a new problem after a good day: told again
    finally:
        undo()
    monkeypatch.setattr(settings, "demo", True)  # the demo lab never emails
    undo = _change_file(record)
    try:
        integrity.run(T0 + timedelta(days=4))
        assert len(outbox) == 2
    finally:
        undo()


# ------------------------------------------------------------------ after a restore

def test_a_restore_forgets_the_last_result_so_the_next_start_checks_again(tmp_path):
    data = tmp_path / "data"
    env = {**os.environ, "BENCHTOP_DATA_DIR": str(data), "BENCHTOP_BACKUP_INTERVAL_HOURS": "0"}
    env.pop("BENCHTOP_BACKUP_DIR", None)
    backend = Path(__file__).resolve().parents[1]
    run = lambda *a: subprocess.run([sys.executable, *a], cwd=backend, env=env, capture_output=True, text=True, timeout=120)  # noqa: E731
    r = run("-c", "from app.db import init_db; init_db(); from app import backup; print(backup.create_backup()['path'])")
    assert r.returncode == 0, r.stderr
    (data / integrity.STATE).write_text(json.dumps({"last": {"ok": False, "at": T0.isoformat(), "checks": []}}))
    r = run("-m", "app.backup", "restore", r.stdout.strip().splitlines()[-1])
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (data / integrity.STATE).exists()


def test_building_the_demo_lab_leaves_no_check_of_the_empty_database(tmp_path):
    """The demo lab is built by a Benchtop running inside the builder; its background check ran on the still-empty
    database and said "all good, none yet", so the real server then skipped the check for a day."""
    data = tmp_path / "data"
    env = {**os.environ, "BENCHTOP_DATA_DIR": str(data), "BENCHTOP_CHECK_INTERVAL_HOURS": "24", "BENCHTOP_BACKUP_INTERVAL_HOURS": "0"}
    r = subprocess.run([sys.executable, "-c", "import time; from app import demo_lab; demo_lab.run = lambda c: time.sleep(3); demo_lab.main()"],
                       cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert not (data / integrity.STATE).exists()
