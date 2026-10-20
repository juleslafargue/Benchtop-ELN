"""v0.13.7: audit events written at the same moment no longer fork the hash chain."""
import threading
import time

from sqlmodel import Session, SQLModel, create_engine, select

from app import audit
from app.db import engine
from app.models import AuditEvent


def test_simultaneous_first_writes_do_not_fork_the_chain(client, admin):
    """The race needed a request whose first write was its audit event (e.g. "Back up now")."""
    gate = threading.Barrier(5)
    errors = []

    def writer(i):
        try:
            with Session(engine) as s:
                gate.wait()
                audit.record(s, None, "race-test", "system", None, n=i)
                time.sleep(0.15)  # hold the transaction open, as a slow request would
                s.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    with Session(engine) as s:
        res = audit.verify_chain(s)
    assert res["ok"] and res["forks"] == 0, res


def test_verifier_accepts_an_old_fork_but_still_catches_tampering(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        for i in range(4):
            audit.record(s, None, "e", "system", None, n=i)
            s.commit()
        # an event chained onto the second-to-last one, as two simultaneous writers did before the fix
        evs = s.exec(select(AuditEvent).order_by(AuditEvent.id)).all()
        fork = AuditEvent(ts=evs[-1].ts, user_id=None, username="system", action="e", object_type="system",
                          object_id=None, details={"n": 99}, prev_hash=evs[-2].hash)
        fork.hash = audit.compute_hash(fork.prev_hash, fork)
        s.add(fork)
        s.commit()
        audit.record(s, None, "e", "system", None, n=5)
        s.commit()
        res = audit.verify_chain(s)
        assert res["ok"] and res["forks"] == 1, res
        # editing an event is still caught
        victim = s.exec(select(AuditEvent).order_by(AuditEvent.id)).all()[1]
        victim.action = "edited"
        s.add(victim)
        s.commit()
        assert not audit.verify_chain(s)["ok"]
        victim.action = "e"
        s.add(victim)
        s.commit()
        assert audit.verify_chain(s)["ok"]
        # deleting an event in the middle is still caught
        s.delete(s.exec(select(AuditEvent).order_by(AuditEvent.id)).all()[2])
        s.commit()
        assert not audit.verify_chain(s)["ok"]


def test_parallel_backups_get_their_own_files(client, admin):
    gate = threading.Barrier(3)
    out = [None] * 3

    def go(i):
        gate.wait()
        out[i] = client.post("/api/admin/backups", headers=admin)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [r.status_code for r in out] == [201, 201, 201], [r.text[:100] for r in out]
    assert len({r.json()["name"] for r in out}) == 3
    with Session(engine) as s:
        assert audit.verify_chain(s)["forks"] == 0


def test_bad_second_copy_paths_are_messages_not_errors(client, admin):
    for path in ("/" + "a" * 5000, "/tmp/" + "b" * 300 + "/x"):
        r = client.put("/api/admin/backups/copy-dir", headers=admin, json={"path": path})
        assert r.status_code == 400, r.text
