"""v0.14.3: fixes from stress-testing witnessing, email and trusted timestamps."""
import base64
import smtplib
import subprocess
import sys
import threading
import time
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app import check, mailer, sitesettings, timestamps, witness
from app.db import engine
from app.models import EmailLog, Experiment, Notification, SignatureTimestamp, User, WitnessRequest, utcnow
from tests.fake_tsa import FakeTSA
from tests.test_v14_witness_timestamps import PW, _emails, _exp, _sign_and_get, _uid, outbox, tsa  # noqa: F401


# ------------------------------------------------------------------ witness requests at the same moment

def _race(n, fn):
    gate, errors = threading.Barrier(n), []

    def run(i):
        try:
            with Session(engine) as s:
                gate.wait()
                fn(s, i)
                time.sleep(0.1)  # hold the transaction open, as a slow request would
                s.commit()
        except HTTPException:
            pass
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors


def test_simultaneous_witness_requests_leave_exactly_one_open(client, users, notebook, admin):
    e = _exp(client, users["alice"], notebook)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW}).raise_for_status()
    ids = [_uid(client, users["bob"]), _uid(client, admin)]

    def ask(s, i):
        witness.request(s, s.get(Experiment, e["id"]), s.get(User, _uid(client, users["alice"])), ids[i % 2])
    _race(4, ask)
    with Session(engine) as s:
        open_ = s.exec(select(WitnessRequest).where(WitnessRequest.experiment_id == e["id"], WitnessRequest.status == "open")).all()
    assert len(open_) == 1


def test_double_clicked_reminder_goes_out_once(client, users, notebook):
    e = _exp(client, users["alice"], notebook)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"],
                json={"password": PW, "witness_id": _uid(client, users["bob"])}).raise_for_status()
    results = []

    def click():
        results.append(client.post(f"/api/experiments/{e['id']}/witness-request/remind", headers=users["alice"]).status_code)
    ts = [threading.Thread(target=click) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [200, 429, 429, 429]
    with Session(engine) as s:
        n = len(s.exec(select(Notification).where(Notification.experiment_id == e["id"], Notification.kind == "witness_reminder")).all())
    assert n == 1


def test_asking_for_a_witness_on_a_record_that_just_left_signed_is_refused(client, users, notebook):
    e = _exp(client, users["alice"], notebook)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"], json={"password": PW}).raise_for_status()
    with Session(engine) as s:
        exp = s.get(Experiment, e["id"])
        client.post(f"/api/experiments/{e['id']}/witness", headers=users["bob"], json={"password": PW}).raise_for_status()
        with pytest.raises(HTTPException) as err:  # this session still thinks it's "signed"
            witness.request(s, exp, s.get(User, _uid(client, users["alice"])), _uid(client, users["bob"]))
        assert err.value.status_code == 409


# ------------------------------------------------------------------ email

def test_a_line_break_in_a_title_doesnt_break_its_email(client, users, notebook, outbox):  # noqa: F811
    _emails(client, users)
    e = _exp(client, users["alice"], notebook, "Line one\nBcc: someone@evil.example")
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"],
                json={"password": PW, "witness_id": _uid(client, users["bob"])}).raise_for_status()
    assert outbox and "Line one Bcc:" in outbox[-1]["Subject"] and outbox[-1]["Bcc"] is None


def test_mail_waits_through_an_outage_and_survives_a_restart(client, users, notebook, outbox, monkeypatch):  # noqa: F811
    _emails(client, users)
    state = {"up": False}

    def flaky(msg, cfg):
        if not state["up"]:
            raise ConnectionRefusedError("server down")
        outbox.append(msg)
    monkeypatch.setattr(mailer, "transport", flaky)
    monkeypatch.setattr(mailer, "_probe", lambda: state["up"])
    e = _exp(client, users["alice"], notebook, "Outage")
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"],
                json={"password": PW, "witness_id": _uid(client, users["bob"])}).raise_for_status()
    with Session(engine) as s:
        row = s.exec(select(EmailLog).order_by(EmailLog.id.desc())).first()
        assert row.status == "queued" and row.next_try and "Couldn't reach" in row.error  # waiting, not lost
        log_id = row.id
    # "restart": the in-memory queue is gone; the message is still in the database. The server comes back.
    assert mailer.sweep() == 0  # still down; its retry gap hasn't passed
    state["up"] = True
    sent = mailer.sweep()  # the reachability check sees the server is back, so it doesn't wait out the gap
    assert sent >= 1 and any(m["To"] == "bob@example.org" and e["eid"] in m["Subject"] for m in outbox)
    with Session(engine) as s:
        assert s.get(EmailLog, log_id).status == "sent"


def test_a_rejected_address_fails_at_once_and_a_wrong_password_waits(client, users, notebook, outbox, monkeypatch):  # noqa: F811
    _emails(client, users)
    monkeypatch.setattr(mailer, "transport", lambda m, c: (_ for _ in ()).throw(
        smtplib.SMTPRecipientsRefused({"bob@example.org": (550, b"no such user")})))
    e = _exp(client, users["alice"], notebook)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"],
                json={"password": PW, "witness_id": _uid(client, users["bob"])}).raise_for_status()
    with Session(engine) as s:
        assert s.exec(select(EmailLog).order_by(EmailLog.id.desc())).first().status == "failed"
    monkeypatch.setattr(mailer, "transport", lambda m, c: (_ for _ in ()).throw(smtplib.SMTPAuthenticationError(535, b"bad")))
    e = _exp(client, users["alice"], notebook)
    client.post(f"/api/experiments/{e['id']}/sign", headers=users["alice"],
                json={"password": PW, "witness_id": _uid(client, users["bob"])}).raise_for_status()
    with Session(engine) as s:  # fixable in Admin → Email, so it waits for the fix
        row = s.exec(select(EmailLog).order_by(EmailLog.id.desc())).first()
        assert row.status == "queued" and row.next_try


def test_queued_mail_is_never_trimmed_from_the_log(client, users, notebook, outbox, monkeypatch):  # noqa: F811
    with Session(engine) as s:
        waiting = EmailLog(to="x@example.org", subject="waiting", kind="test", context="{}", attempts=1,
                           next_try=utcnow() + timedelta(hours=1))
        s.add(waiting)
        s.commit()
        wid = waiting.id
        for i in range(mailer.KEEP + 5):
            s.add(EmailLog(to="y@example.org", subject=f"old {i}", kind="test", status="sent"))
        s.commit()
        s.add(EmailLog(to="z@example.org", subject="now", kind="test", context="{}", attempts=0))
        s.commit()
        last = s.exec(select(EmailLog).order_by(EmailLog.id.desc())).first().id
    mailer.deliver(last)
    with Session(engine) as s:
        assert s.get(EmailLog, wid) is not None


def test_email_settings_refuse_line_breaks_and_urls_as_host(client, admin):
    assert client.put("/api/admin/email", headers=admin, json={"from_name": "Lab\nBcc: x@y.z"}).status_code == 422
    assert client.put("/api/admin/email", headers=admin, json={"host": "https://smtp.gmail.com"}).status_code == 422


# ------------------------------------------------------------------ trusted timestamps

def test_the_health_check_verifies_timestamps_in_a_fresh_process(client, users, notebook, tsa, tmp_path):  # noqa: F811
    """`./benchtop check` runs in its own process, where nothing had loaded the timestamp definitions."""
    exp = _sign_and_get(client, users, notebook)
    with Session(engine) as s:
        row = s.exec(select(SignatureTimestamp).where(SignatureTimestamp.signature_id == exp["signatures"][-1]["id"])).one()
    (tmp_path / "t.tst").write_bytes(base64.b64decode(row.token))
    code = ("import sys; sys.path.insert(0, '.'); from app import timestamps as t; "
            f"r = t.verify(open({str(tmp_path / 't.tst')!r}, 'rb').read(), bytes.fromhex({row.digest!r})); "
            "print(r['ok'], r['error'])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=".")
    assert out.stdout.startswith("True"), out.stdout + out.stderr


def test_retries_go_to_the_service_chosen_now(client, users, notebook, tsa, monkeypatch):  # noqa: F811
    tsa.mode = "down"
    exp = _sign_and_get(client, users, notebook)
    urls = []
    tsa.mode = "ok"
    monkeypatch.setattr(timestamps, "transport", lambda url, body: urls.append(url) or tsa(url, body))
    sitesettings.update("timestamps", {"tsa_url": "http://working-tsa.example.org"})
    timestamps.retry_pending()
    assert urls and set(urls) == {"http://working-tsa.example.org"}
    exp = client.get(f"/api/experiments/{exp['id']}", headers=users["alice"]).json()
    assert exp["signatures"][-1]["timestamp"]["status"] == "ok"


def test_an_unreachable_service_isnt_retried_for_every_signature(client, users, notebook, tsa):  # noqa: F811
    tsa.mode = "down"
    for _ in range(3):
        _sign_and_get(client, users, notebook)  # each new signature still tries once
    before = tsa.requests
    with Session(engine) as s:
        pending = s.exec(select(SignatureTimestamp.id).where(SignatureTimestamp.status == "pending")).all()
    timestamps._skip_until = time.monotonic() + 60
    for i in pending:
        timestamps._submit(i)  # e.g. hourly retry racing a pause: retries wait
    assert tsa.requests == before
    tsa.mode = "ok"
    _sign_and_get(client, users, notebook)  # a new signature gets through, and the rest catch up with it
    with Session(engine) as s:
        assert not s.exec(select(SignatureTimestamp).where(SignatureTimestamp.status == "pending")).all()


def test_a_huge_reply_is_cut_off(monkeypatch):
    import httpx
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"\0" * (timestamps.MAX_REPLY + 10))))
    monkeypatch.setattr(httpx, "stream", lambda method, url, **kw: client.stream(method, url, content=kw.get("content")))
    with pytest.raises(timestamps.TimestampError, match="far more"):
        timestamps._post("http://tsa.example.org", b"x")


def test_certificates_must_lead_to_a_trusted_authority(client, users, notebook, monkeypatch):
    ca_tsa = FakeTSA(chain=True)
    monkeypatch.setattr(timestamps, "synchronous", True)
    monkeypatch.setattr(timestamps, "transport", ca_tsa)
    monkeypatch.setattr(timestamps, "_skip_until", 0.0)
    monkeypatch.setattr(timestamps, "_roots_cache", {ca_tsa.root.subject.public_bytes(): [ca_tsa.root]})
    monkeypatch.delenv("BENCHTOP_TIMESTAMPS", raising=False)
    sitesettings.update("timestamps", {"enabled": True, "tsa_url": "http://tsa.example.org"})
    try:
        exp = _sign_and_get(client, users, notebook)
        sid = exp["signatures"][-1]["id"]
        with Session(engine) as s:
            row = s.exec(select(SignatureTimestamp).where(SignatureTimestamp.signature_id == sid)).one()
        token = base64.b64decode(row.token)
        assert timestamps.verify(token, bytes.fromhex(row.digest))["trusted"]
        # a self-signed service (like FreeTSA) verifies but isn't traced to an authority
        own = FakeTSA()
        tok2, info2 = _token_from(own, row.digest)
        assert info2["ok"] and not info2["trusted"]
        # swap in a token re-made with a home-made certificate: the health check notices
        with Session(engine) as s:
            r = s.get(SignatureTimestamp, row.id)
            r.token = base64.b64encode(tok2).decode()
            s.add(r)
            s.commit()
        item = next(c for c in check.check_live()["checks"] if c["name"] == "Trusted timestamps")
        assert not item["ok"] and str(sid) in item["detail"]
        with Session(engine) as s:
            r = s.get(SignatureTimestamp, row.id)
            r.token = base64.b64encode(token).decode()
            s.add(r)
            s.commit()
        item = next(c for c in check.check_live()["checks"] if c["name"] == "Trusted timestamps")
        assert item["ok"], item
    finally:
        sitesettings.update("timestamps", {"enabled": False})


def _req(digest_hex):
    return timestamps.build_request(bytes.fromhex(digest_hex), 1)


def _token_from(fake, digest_hex):
    from asn1crypto import tsp
    raw = fake("", _req(digest_hex))
    token = tsp.TimeStampResp.load(raw)["time_stamp_token"].dump()
    return token, timestamps.verify(token, bytes.fromhex(digest_hex))
