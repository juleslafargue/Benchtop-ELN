"""0.17: sample codes (JL-013) tell Benchtop where instrument files belong."""
import threading
from datetime import datetime, timedelta

import pytest

from app import instruments

PW = "lab-bench-pw-2026"
T0 = datetime(2031, 1, 1, 9, 0, 0)


def scan(seconds: float):
    return instruments.scan_all(now=T0 + timedelta(seconds=seconds))


@pytest.fixture(scope="module")
def prefixed(client, users, admin):
    """alice uses QA, bob uses QB (unusual initials, so other tests' file names never match them)."""
    for u, p in (("alice", "QA"), ("bob", "QB")):
        r = client.put("/api/auth/me/sample-prefix", headers=users[u], json={"prefix": p})
        assert r.status_code == 200 and r.json()["sample_prefix"] == p, r.text
    yield users
    for u in ("alice", "bob"):
        client.put("/api/auth/me/sample-prefix", headers=users[u], json={"prefix": None})


@pytest.fixture()
def folder(tmp_path, client, admin):
    d = tmp_path / "share"
    d.mkdir()
    r = client.post("/api/instruments", headers=admin, json={"name": "Sample code test", "kind": "files", "folder": str(d)})
    assert r.status_code == 201, r.text
    scan(0)
    yield d
    client.delete(f"/api/instruments/{r.json()['id']}", headers=admin)


def new_exp(client, h, nb, title="Sample code exp"):
    r = client.post("/api/experiments", headers=h, json={"title": title, "notebook_id": nb["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def code(client, h, exp, number=None):
    r = client.post(f"/api/experiments/{exp['id']}/samples", headers=h, json={} if number is None else {"number": number})
    assert r.status_code == 201, r.text
    return r.json()


def arrive(folder, name, data=b"x"):
    (folder / name).write_bytes(data)
    scan(10)
    scan(300)


def files_of(client, h, exp):
    return [a["filename"] for a in client.get(f"/api/experiments/{exp['id']}", headers=h).json()["attachments"]]


def inbox(client, h):
    return {i["path"]: i for i in client.get("/api/inbox", headers=h).json()}


def test_choosing_a_prefix(client, users, admin, prefixed):
    h = users["alice"]
    for bad in ("J", "1AB", "TOOLONGX", "J-L", "exp"):
        assert client.put("/api/auth/me/sample-prefix", headers=h, json={"prefix": bad}).status_code == 422, bad
    r = client.put("/api/auth/me/sample-prefix", headers=users["vic"], json={"prefix": "qa"})
    assert r.status_code == 409 and "Alice" in r.json()["detail"]
    assert client.get("/api/auth/me", headers=h).json()["sample_prefix"] == "QA"


def test_new_sample_codes_count_up(client, prefixed, notebook):
    h = prefixed["alice"]
    a, b = new_exp(client, h, notebook), new_exp(client, h, notebook)
    first = code(client, h, a)["code"]
    second = code(client, h, b)["code"]
    assert first.startswith("QA-") and int(second[3:]) == int(first[3:]) + 1
    assert [s["code"] for s in client.get(f"/api/experiments/{a['id']}", headers=h).json()["samples"]] == [first]
    # a number already written on a vial
    assert code(client, h, a, 900)["code"] == "QA-900"
    r = client.post(f"/api/experiments/{b['id']}/samples", headers=h, json={"number": 900})
    assert r.status_code == 409 and a["eid"] in r.json()["detail"]
    # not on someone else's experiment, not for read-only accounts, not without a prefix
    assert client.post(f"/api/experiments/{a['id']}/samples", headers=prefixed["bob"], json={}).status_code == 403
    assert client.post(f"/api/experiments/{a['id']}/samples", headers=prefixed["vic"], json={}).status_code == 403


def test_two_clicks_at_once_get_different_numbers(client, prefixed, notebook):
    h = prefixed["alice"]
    exp = new_exp(client, h, notebook)
    gate, out = threading.Barrier(2), [None, None]

    def go(i):
        gate.wait()
        out[i] = client.post(f"/api/experiments/{exp['id']}/samples", headers=h, json={})
    ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [r.status_code for r in out] == [201, 201]
    assert out[0].json()["code"] != out[1].json()["code"]


def test_a_file_with_a_sample_code_goes_to_its_experiment(client, prefixed, notebook, folder):
    h = prefixed["alice"]
    exp = new_exp(client, h, notebook)
    c = code(client, h, exp)["code"]
    n = int(c[3:])
    for name in (f"{c}-20261021 1H.jdx", f"qa_{n} HPLC.csv", f"QA {n:05d} IR.csv"):
        arrive(folder, name, name.encode())  # different contents: identical files are only attached once
    assert sorted(files_of(client, h, exp)) == sorted([f"{c}-20261021 1H.jdx", f"qa_{n} HPLC.csv", f"QA {n:05d} IR.csv"])


def test_no_separator_isnt_a_code(client, prefixed, notebook, folder):
    h = prefixed["alice"]
    exp = new_exp(client, h, notebook)
    c = code(client, h, exp)["code"]
    arrive(folder, f"qa{int(c[3:])} quick.csv")
    assert not files_of(client, h, exp)
    assert "No experiment number" in inbox(client, h)[f"qa{int(c[3:])} quick.csv"]["note"]


def test_an_unknown_number_goes_only_to_its_owner(client, prefixed, admin, notebook, folder):
    arrive(folder, "QA-777777 mystery.pdf")
    mine = inbox(client, prefixed["alice"])["QA-777777 mystery.pdf"]
    assert mine["for_you"] and "QA-777777 isn't a sample code" in mine["note"]
    assert "QA-777777 mystery.pdf" not in inbox(client, prefixed["bob"])
    assert "QA-777777 mystery.pdf" in inbox(client, admin)
    notes = client.get("/api/notifications", headers=prefixed["alice"]).json()["items"]
    assert any(n["kind"] == "inbox" and "sample code" in n["text"] for n in notes)


def test_a_retired_code_points_nowhere_and_can_be_given_again(client, prefixed, notebook, folder):
    h = prefixed["alice"]
    wrong, right = new_exp(client, h, notebook, "Wrong one"), new_exp(client, h, notebook, "Right one")
    sc = code(client, h, wrong, 4242)
    assert client.delete(f"/api/samples/{sc['id']}", headers=h).status_code == 200
    assert client.get(f"/api/experiments/{wrong['id']}", headers=h).json()["samples"] == []
    arrive(folder, "QA-4242 a.csv")
    assert not files_of(client, h, wrong) and inbox(client, h)["QA-4242 a.csv"]["for_you"]
    # a new code never reuses it
    assert code(client, h, right)["code"] != "QA-4242"
    # given again, on purpose, to the right experiment
    assert code(client, h, right, 4242)["code"] == "QA-4242"
    arrive(folder, "QA-4242 b.csv")
    assert files_of(client, h, right) == ["QA-4242 b.csv"]


def test_number_and_code_must_agree(client, prefixed, notebook, folder):
    h = prefixed["alice"]
    a, b = new_exp(client, h, notebook), new_exp(client, h, notebook)
    ca = code(client, h, a)["code"]
    arrive(folder, f"{a['eid']} {ca} agree.csv")
    assert files_of(client, h, a) == [f"{a['eid']} {ca} agree.csv"]
    arrive(folder, f"{b['eid']} {ca} disagree.csv")
    item = inbox(client, h)[f"{b['eid']} {ca} disagree.csv"]
    assert "more than one experiment" in item["note"] and {m["eid"] for m in item["matched"]} == {a["eid"], b["eid"]}


def test_a_code_on_a_signed_record_offers_a_follow_up(client, prefixed, notebook, folder):
    h = prefixed["alice"]
    exp = new_exp(client, h, notebook)
    c = code(client, h, exp)["code"]
    assert client.post(f"/api/experiments/{exp['id']}/sign", headers=h, json={"password": PW}).status_code == 200
    arrive(folder, f"{c} HRMS.pdf")
    item = inbox(client, h)[f"{c} HRMS.pdf"]
    assert f"Matched {c} ({exp['eid']})" in item["note"] and item["follow_up"]
    # and codes can't be added to a signed record
    assert client.post(f"/api/experiments/{exp['id']}/samples", headers=h, json={}).status_code == 409


def test_old_codes_still_work_after_a_new_prefix(client, prefixed, notebook, folder):
    h = prefixed["bob"]
    exp = new_exp(client, h, notebook)
    c = code(client, h, exp)["code"]
    assert c.startswith("QB-")
    assert client.put("/api/auth/me/sample-prefix", headers=h, json={"prefix": "QBX"}).status_code == 200
    arrive(folder, f"{c} old label.csv")
    assert files_of(client, h, exp) == [f"{c} old label.csv"]
    # and nobody else can take the old prefix while codes with it exist
    assert client.put("/api/auth/me/sample-prefix", headers=prefixed["vic"], json={"prefix": "QB"}).status_code == 409
    client.put("/api/auth/me/sample-prefix", headers=h, json={"prefix": "QB"})
