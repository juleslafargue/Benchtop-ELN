"""v0.14: witness requests with email, and RFC 3161 trusted timestamps on signatures."""
import base64
import io
import json
import subprocess
import zipfile
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app import check, mailer, sitesettings, timestamps, witness
from app.db import engine
from app.models import EmailLog, Notification, SignatureTimestamp, User, WitnessRequest, utcnow
from tests.fake_tsa import FakeTSA

PW = "lab-bench-pw-2026"


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "synchronous", True)
    monkeypatch.setattr(mailer, "transport", lambda msg, cfg: sent.append(msg))
    sitesettings.update("email", {"host": "smtp.example.org", "from_address": "eln@example.org", "password": "s3cret-app-pw"})
    yield sent
    sitesettings.update("email", {"host": "", "from_address": ""})


@pytest.fixture()
def tsa(monkeypatch):
    fake = FakeTSA()
    monkeypatch.delenv("BENCHTOP_TIMESTAMPS", raising=False)
    monkeypatch.setattr(timestamps, "synchronous", True)
    monkeypatch.setattr(timestamps, "transport", fake)
    sitesettings.update("timestamps", {"enabled": True, "tsa_url": "http://tsa.example.org"})
    yield fake
    sitesettings.update("timestamps", {"enabled": False})


def _emails(client, users):
    """Give alice and bob email addresses."""
    client.patch("/api/auth/me", headers=users["alice"], json={"email": "alice@example.org"})
    client.patch("/api/auth/me", headers=users["bob"], json={"email": "bob@example.org"})


def _exp(client, h, nb, title="Witness flow"):
    return client.post("/api/experiments", headers=h, json={"title": title, "notebook_id": nb["id"]}).json()


def _uid(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


# ------------------------------------------------------------------ witness requests

def test_candidates_exclude_author_and_read_only(client, users, notebook):
    e = _exp(client, users["alice"], notebook)
    names = {c["username"] for c in client.get(f"/api/experiments/{e['id']}/witness-candidates", headers=users["alice"]).json()["candidates"]}
    assert "bob" in names and "alice" not in names and "vic" not in names


def test_sign_with_witness_notifies_and_emails(client, users, notebook, outbox):
    _emails(client, users)
    e = _exp(client, users["alice"], notebook)
    bob = _uid(client, users["bob"])
    r = client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW, "witness_id": bob})
    assert r.status_code == 200, r.text
    assert r.json()["witness_request"]["witness"]["username"] == "bob"
    notes = client.get("/api/notifications", headers=users["bob"]).json()["items"]
    assert any(n["kind"] == "witness_request" and (n["experiment"] or {}).get("id") == e["id"] for n in notes)
    assert outbox and outbox[-1]["To"] == "bob@example.org" and e["eid"] in outbox[-1]["Subject"]
    body = outbox[-1].get_body(("plain",)).get_content()
    assert f"/experiments/{e['id']}" in body
    # bob's dashboard puts it first and marks it as his
    first = client.get("/api/dashboard", headers=users["bob"]).json()["lists"]["awaiting"][0]
    assert first["id"] == e["id"] and first["assigned_to_me"]
    mine = client.get("/api/dashboard", headers=users["alice"]).json()["lists"]["waiting"]
    assert any(x["id"] == e["id"] and x["witness"]["username"] == "bob" for x in mine)
    # bob witnesses: alice hears back
    assert client.post(f"/api/experiments/{e['id']}/witness", headers=users["bob"], json={"password": PW}).status_code == 200
    assert outbox[-1]["To"] == "alice@example.org" and "witnessed" in outbox[-1]["Subject"]
    with Session(engine) as s:
        req = s.exec(select(WitnessRequest).where(WitnessRequest.experiment_id == e["id"])).one()
        assert req.status == "done" and req.outcome == "witnessed"


def test_bad_witness_choice_leaves_record_unsigned(client, users, notebook):
    e = _exp(client, users["alice"], notebook)
    vic = _uid(client, users["vic"])
    r = client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW, "witness_id": vic})
    assert r.status_code == 422
    assert client.get(f"/api/experiments/{e['id']}", headers=users["alice"]).json()["status"] == "open"
    r = client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW, "witness_id": _uid(client, users["alice"])})
    assert r.status_code == 422  # not yourself


def test_reassign_remind_and_return(client, users, notebook, admin, outbox):
    _emails(client, users)
    e = _exp(client, users["alice"], notebook)
    bob, adm = _uid(client, users["bob"]), _uid(client, admin)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW, "witness_id": bob})
    assert client.put(f"/api/experiments/{e['id']}/witness-request", headers=users["bob"], json={"witness_id": adm}).status_code == 403
    r = client.put(f"/api/experiments/{e['id']}/witness-request", headers=users["alice"], json={"witness_id": adm})
    assert r.status_code == 200 and r.json()["witness"]["id"] == adm
    assert any(n["kind"] == "witness_cancelled" for n in client.get("/api/notifications", headers=users["bob"]).json()["items"])
    assert client.post(f"/api/experiments/{e['id']}/witness-request/remind", headers=users["alice"]).status_code == 200
    assert client.post(f"/api/experiments/{e['id']}/witness-request/remind", headers=users["alice"]).status_code == 429
    # the newly asked admin returns it with a reason: alice gets the reason by email
    r = client.post(f"/api/experiments/{e['id']}/reject", headers=admin, json={"password": "adminpass", "comment": "add the 13C data"})
    assert r.status_code == 200, r.text
    assert outbox[-1]["To"] == "alice@example.org" and "add the 13C data" in outbox[-1].get_body(("plain",)).get_content()


def test_withdraw_tells_the_witness_and_reminders_are_scheduled(client, users, notebook, outbox):
    _emails(client, users)
    bob = _uid(client, users["bob"])
    e = _exp(client, users["alice"], notebook)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW, "witness_id": bob})
    with Session(engine) as s:  # four days pass
        req = s.exec(select(WitnessRequest).where(WitnessRequest.experiment_id == e["id"], WitnessRequest.status == "open")).one()
        req.created_at = utcnow() - timedelta(days=4)
        req.due_at = utcnow() - timedelta(days=1)
        s.add(req)
        s.commit()
    before = len(outbox)
    assert witness.send_due_reminders() >= 1
    assert len(outbox) > before and "Reminder" in outbox[-1]["Subject"]
    assert witness.send_due_reminders() == 0  # not again until the next interval
    assert client.get(f"/api/experiments/{e['id']}", headers=users["alice"]).json()["witness_request"]["overdue"]
    client.post(f"/api/experiments/{e['id']}/withdraw", headers=users["alice"], json={"password": PW})
    notes = client.get("/api/notifications", headers=users["bob"]).json()["items"]
    assert any(n["kind"] == "witness_cancelled" and "withdrawn" in n["text"] for n in notes)


def test_email_preferences_and_no_server(client, users, notebook, outbox):
    _emails(client, users)
    client.patch("/api/auth/me", headers=users["bob"], json={"preferences": {"email_notify": {"witness": False}}})
    e = _exp(client, users["alice"], notebook)
    before = len(outbox)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW, "witness_id": _uid(client, users["bob"])})
    assert len(outbox) == before  # bob turned witness emails off; he still gets the in-app notification
    client.patch("/api/auth/me", headers=users["bob"], json={"preferences": {"email_notify": {"witness": True}}})


def test_email_settings_keep_the_password_secret(client, admin, outbox):
    pub = client.get("/api/admin/email", headers=admin).json()
    assert pub["password"] == "" and pub["password_set"] is True
    from app.config import settings
    raw = (settings.data_dir / "site-settings.json").read_text()
    assert "s3cret-app-pw" not in raw
    assert sitesettings.get("email")["password"] == "s3cret-app-pw"
    # blank password in the form keeps the stored one
    client.put("/api/admin/email", headers=admin, json={"host": "smtp.example.org", "from_address": "eln@example.org"})
    assert sitesettings.get("email")["password"] == "s3cret-app-pw"
    assert client.post("/api/admin/email/test", headers=admin, json={"to": "me@example.org"}).status_code == 200
    assert client.put("/api/admin/email", headers=admin, json={"security": "telnet"}).status_code == 422


def test_failed_sending_is_logged(client, users, notebook, admin, outbox, monkeypatch):
    import smtplib

    def refuse(msg, cfg):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
    monkeypatch.setattr(mailer, "transport", refuse)
    _emails(client, users)
    e = _exp(client, users["alice"], notebook)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW, "witness_id": _uid(client, users["bob"])})
    log = client.get("/api/admin/email", headers=admin).json()["log"]
    assert log[0]["status"] == "failed" and "app password" in log[0]["error"]
    r = client.post("/api/admin/email/test", headers=admin, json={"to": "me@example.org"})
    assert r.status_code == 400 and "password" in r.json()["detail"]


# ------------------------------------------------------------------ trusted timestamps

def _sign_and_get(client, users, notebook):
    e = _exp(client, users["alice"], notebook, "Timestamped")
    r = client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW})
    assert r.status_code == 200, r.text
    return client.get(f"/api/experiments/{e['id']}", headers=users["alice"]).json()


def test_signing_gets_a_trusted_timestamp_that_openssl_accepts(client, users, notebook, tsa, tmp_path):
    exp = _sign_and_get(client, users, notebook)
    sig = exp["signatures"][-1]
    assert sig["timestamp"]["status"] == "ok" and sig["timestamp"]["tsa"] == "Benchtop Test TSA"
    assert abs(sig["timestamp"]["delay_seconds"]) < 60
    z = client.get(f"/api/signatures/{sig['id']}/timestamp", headers=users["alice"])
    assert z.status_code == 200
    files = zipfile.ZipFile(io.BytesIO(z.content))
    payload = json.loads(files.read("signature.json"))
    assert payload["record"] == exp["eid"] and payload["content_sha256"] == sig["content_hash"]
    for n in ("signature.json", "token.tst"):
        (tmp_path / n).write_bytes(files.read(n))
    (tmp_path / "ca.pem").write_bytes(tsa.cert_pem)
    out = subprocess.run(["openssl", "ts", "-verify", "-data", str(tmp_path / "signature.json"), "-in", str(tmp_path / "token.tst"),
                          "-token_in", "-CAfile", str(tmp_path / "ca.pem")], capture_output=True, text=True)
    assert "Verification: OK" in out.stdout + out.stderr, out.stdout + out.stderr
    report = client.get(f"/api/experiments/{exp['id']}/export", headers=users["alice"]).text
    assert "Trusted timestamp" in report and "Benchtop Test TSA" in report
    assert client.get(f"/api/experiments/{exp['id']}/export?format=pdf", headers=users["alice"]).status_code == 200


def test_offline_signing_works_and_the_timestamp_follows(client, users, notebook, tsa):
    tsa.mode = "down"
    exp = _sign_and_get(client, users, notebook)
    assert exp["status"] == "signed" and exp["signatures"][-1]["timestamp"]["status"] == "pending"
    tsa.mode = "ok"
    timestamps.retry_pending()
    exp = client.get(f"/api/experiments/{exp['id']}", headers=users["alice"]).json()
    assert exp["signatures"][-1]["timestamp"]["status"] == "ok"


def test_bad_tokens_are_never_stored(client, users, notebook, tsa):
    for mode in ("tamper", "wrong_digest", "garbage", "refuse"):
        tsa.mode = mode
        exp = _sign_and_get(client, users, notebook)
        t = exp["signatures"][-1]["timestamp"]
        assert t["status"] == "pending" and t["error"], (mode, t)
    tsa.mode = "ok"


def test_health_check_verifies_timestamps_and_catches_tampering(client, users, notebook, tsa):
    exp = _sign_and_get(client, users, notebook)
    sid = exp["signatures"][-1]["id"]
    res = check.check_live()
    item = next(c for c in res["checks"] if c["name"] == "Trusted timestamps")
    assert item["ok"], item
    with Session(engine) as s:  # someone edits the stored timestamp's payload
        row = s.exec(select(SignatureTimestamp).where(SignatureTimestamp.signature_id == sid)).one()
        original = row.payload
        row.payload = original.replace(exp["eid"], "EXP-99999")
        s.add(row)
        s.commit()
    item = next(c for c in check.check_live()["checks"] if c["name"] == "Trusted timestamps")
    assert not item["ok"] and str(sid) in item["detail"]
    with Session(engine) as s:
        row = s.exec(select(SignatureTimestamp).where(SignatureTimestamp.signature_id == sid)).one()
        row.payload = original
        s.add(row)
        s.commit()


def test_admin_timestamp_settings(client, admin, tsa):
    st = client.get("/api/admin/timestamps", headers=admin).json()
    assert st["enabled"] and st["available"] and len(st["services"]) >= 3
    assert client.post("/api/admin/timestamps/test", headers=admin, json={}).json()["ok"]
    assert client.put("/api/admin/timestamps", headers=admin, json={"tsa_url": "ftp://nope"}).status_code == 422
    tsa.mode = "refuse"
    r = client.post("/api/admin/timestamps/test", headers=admin, json={}).json()
    assert not r["ok"] and "refused" in r["error"]
    tsa.mode = "ok"


def test_timestamps_off_means_no_requests(client, users, notebook, monkeypatch):
    fake = FakeTSA()
    monkeypatch.setattr(timestamps, "transport", fake)
    exp = _sign_and_get(client, users, notebook)  # BENCHTOP_TIMESTAMPS=off in tests
    assert exp["signatures"][-1]["timestamp"] is None and fake.requests == 0


def test_email_uses_its_own_trusted_certificates():
    """python.org Python on macOS has no CA certificates until "Install Certificates.command" runs."""
    import certifi
    ctx = mailer.tls_context()
    assert len(ctx.get_ca_certs()) > 50, "certifi's bundle wasn't loaded"
    assert ctx.verify_mode.name == "CERT_REQUIRED" and ctx.check_hostname
    import ssl
    msg = mailer._explain(ssl.SSLCertVerificationError(1, "certificate verify failed: unable to get local issuer certificate"))
    assert "Install Certificates" in msg and certifi.where()
