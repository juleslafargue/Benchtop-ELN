"""0.22: choosing which of an instrument folder's older data to bring in.

What's in a folder when it's added is left alone (so a departmental drive doesn't flood the Inbox with years of
spectra). Admins can now see that list, with what would happen to each (attached to which draft, or the Inbox),
filter it, and take the ones they want; each is then handled like any new arrival. Only admins: file names can be
revealing, and these were never matched to anyone. The row on the admin page says what "left alone" means.
"""
from __future__ import annotations

import shutil

from sqlmodel import Session, select

from app.db import engine
from app.models import AuditEvent
from test_v16_instruments import add, bruker, folder, get, inbox, new_exp, people, scan, sign  # noqa: F401  (fixtures)


def old(client, h, inst, **params):
    return client.get(f"/api/instruments/{inst['id']}/old", headers=h, params=params)


def take(client, h, inst, paths):
    return client.post(f"/api/instruments/{inst['id']}/take", headers=h, json={"paths": paths})


def counts(client, admin, inst):
    return next(i for i in client.get("/api/instruments", headers=admin).json()["instruments"] if i["id"] == inst["id"])["counts"]


def instrument_section(client, h, exp):
    return [e for e in get(client, h, exp)["entries"] if e["title"] == "Instrument data"]


def test_old_data_is_listed_with_where_it_would_go_and_only_the_chosen_ones_are_taken(client, people, admin, notebook, folder, add):
    a = people["alice"]
    draft, other = new_exp(client, a, notebook, "Old data, wanted"), new_exp(client, a, notebook, "Old data, not wanted")
    bruker(folder, f"{draft['eid']} crude", 10)
    bruker(folder, f"{other['eid']} pure", 11)
    bruker(folder, "no number here", 12)
    inst = add(folder, kind="bruker_nmr")
    scan(0)
    assert counts(client, admin, inst).get("baseline") == 3

    r = old(client, admin, inst)
    assert r.status_code == 200, r.text
    listed = r.json()
    assert listed["total"] == 3
    by = {x["path"]: x for x in listed["items"]}
    assert set(by) == {f"{draft['eid']} crude/10", f"{other['eid']} pure/11", "no number here/12"}
    assert by[f"{draft['eid']} crude/10"]["action"] == "attach" and by[f"{draft['eid']} crude/10"]["experiment"] == draft["eid"]
    assert by["no number here/12"]["action"] == "inbox" and by["no number here/12"]["note"]
    assert all(not x["gone"] and x["size"] > 0 and x["modified"] for x in listed["items"])
    assert [x["path"] for x in old(client, admin, inst, q=draft["eid"].lower()).json()["items"]] == [f"{draft['eid']} crude/10"]

    r = take(client, admin, inst, [f"{draft['eid']} crude/10", "no number here/12"])
    assert r.status_code == 200, r.text
    assert sorted(r.json()["taken"]) == [f"{draft['eid']} crude/10", "no number here/12"]
    sec = instrument_section(client, a, draft)
    assert len(sec) == 1 and len(sec[0]["content"]["attachment_ids"]) == 1  # attached straight away
    assert instrument_section(client, a, other) == []  # not chosen: still left alone
    assert any(x["path"] == "no number here/12" for x in inbox(client, a))  # unmatched: the Inbox, as any arrival
    assert [x["path"] for x in old(client, admin, inst).json()["items"]] == [f"{other['eid']} pure/11"]
    c = counts(client, admin, inst)
    assert c.get("baseline") == 1 and c.get("attached") == 1 and c.get("inbox") == 1

    with Session(engine) as s:
        ev = s.exec(select(AuditEvent).where(AuditEvent.action == "take_old").order_by(AuditEvent.id.desc())).first()
        assert ev is not None and ev.details["count"] == 2 and f"{draft['eid']} crude/10" in ev.details["paths"]


def test_taking_twice_gone_or_unknown_paths_and_signed_records(client, people, admin, notebook, folder, add):
    a = people["alice"]
    signed = new_exp(client, a, notebook, "Signed before its data came")
    sign(client, a, signed)
    bruker(folder, f"{signed['eid']} late", 20)
    bruker(folder, "deleted later", 21)
    inst = add(folder, kind="bruker_nmr")
    scan(0)
    shutil.rmtree(folder / "deleted later")
    items = {x["path"]: x for x in old(client, admin, inst).json()["items"]}
    assert items["deleted later/21"]["gone"] is True
    assert items[f"{signed['eid']} late/20"]["action"] == "inbox"  # signed records are never changed, old data or not

    r = take(client, admin, inst, [f"{signed['eid']} late/20", "deleted later/21", "never/existed", f"{signed['eid']} late/20"]).json()
    assert r == {"taken": [f"{signed['eid']} late/20"], "gone": ["deleted later/21"], "not_old": ["never/existed"]}
    assert instrument_section(client, a, signed) == []
    assert take(client, admin, inst, [f"{signed['eid']} late/20"]).json()["not_old"] == [f"{signed['eid']} late/20"]  # once only


def test_only_admins_and_not_while_paused(client, people, admin, notebook, folder, add):
    bruker(folder, "anything", 30)
    inst = add(folder, kind="bruker_nmr")
    scan(0)
    for who in ("alice", "vic"):
        assert old(client, people[who], inst).status_code == 403
        assert take(client, people[who], inst, ["anything/30"]).status_code == 403
    client.patch(f"/api/instruments/{inst['id']}", headers=admin, json={"enabled": False})
    r = take(client, admin, inst, ["anything/30"])
    assert r.status_code == 409 and "paused" in r.json()["detail"]
    assert take(client, admin, inst, ["x"] * 1001).status_code in (409, 422)
    assert old(client, admin, {"id": 999999}).status_code == 404
