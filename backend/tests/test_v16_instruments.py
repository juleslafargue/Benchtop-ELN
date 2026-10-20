"""0.16: instrument folders, the Inbox, follow-ups and "Not mine"."""
import io
import os
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app import check, instruments
from app.config import settings
from app.db import engine
from app.models import InstrumentFile, User

PW = "lab-bench-pw-2026"
T0 = datetime(2030, 1, 1, 9, 0, 0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def scan(seconds: float):
    return instruments.scan_all(now=at(seconds))


@pytest.fixture(scope="module")
def people(client, admin, users):
    """alice and bob (scientists), vic (read-only), and carl, who is in no group."""
    client.post("/api/users", headers=admin, json={"username": "carl", "password": PW, "must_change": False,
                                                   "full_name": "Carl", "role": "scientist"})
    r = client.post("/api/auth/login", json={"username": "carl", "password": PW})
    ids = {u["username"]: u["id"] for u in client.get("/api/users", headers=admin).json()}
    return {**users, "carl": {"Authorization": f"Bearer {r.json()['token']}"}, "ids": ids}


@pytest.fixture()
def folder(tmp_path):
    d = tmp_path / "instrument"
    d.mkdir()
    return d


@pytest.fixture()
def add(client, admin):
    made = []

    def _add(folder: Path, kind="files", **kw):
        r = client.post("/api/instruments", headers=admin, json={"name": kw.pop("name", "Test instrument"), "kind": kind,
                                                                  "folder": str(folder), **kw})
        assert r.status_code == 201, r.text
        made.append(r.json()["id"])
        return r.json()
    yield _add
    for i in made:  # stop watching, so later tests' scans don't look at these folders
        client.delete(f"/api/instruments/{i}", headers=admin)


def new_exp(client, h, nb, title="Instrument exp"):
    r = client.post("/api/experiments", headers=h, json={"title": title, "notebook_id": nb["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def get(client, h, exp):
    return client.get(f"/api/experiments/{exp['id']}", headers=h).json()


def sign(client, h, exp):
    r = client.post(f"/api/experiments/{exp['id']}/sign", headers=h, json={"password": PW})
    assert r.status_code == 200, r.text


def inbox(client, h, state="inbox"):
    return client.get("/api/inbox", headers=h, params={"state": state}).json()


def bruker(root: Path, sample: str, expno: int, title: str = "") -> Path:
    d = root / sample / str(expno)
    (d / "pdata" / "1").mkdir(parents=True)
    (d / "acqus").write_text("##$NUC1= <1H>\n##$SW_H= 8000\n##$BF1= 400.13\n##$TD= 16\n##$BYTORDA= 0\n##$DTYPA= 0\n")
    (d / "fid").write_bytes(os.urandom(64))
    (d / "pdata" / "1" / "title").write_text(title)
    return d


# ------------------------------------------------------------------ arriving and attaching

def test_new_file_is_attached_once_it_stops_changing(client, people, admin, notebook, folder, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    (folder / f"old {exp['eid']}.pdf").write_bytes(b"%PDF already here")  # there before the folder was added
    add(folder)
    scan(0)
    data = b"%PDF-1.4 HPLC report"
    (folder / f"{exp['eid']} hplc.pdf").write_bytes(data[:10])  # still being copied
    scan(60)
    (folder / f"{exp['eid']} hplc.pdf").write_bytes(data)  # grows
    scan(120)
    assert not get(client, h, exp)["attachments"], "attached before it stopped changing"
    scan(200)
    assert not get(client, h, exp)["attachments"], "attached before the quiet time had passed since the last change"
    scan(260)
    e = get(client, h, exp)
    assert [a["filename"] for a in e["attachments"]] == [f"{exp['eid']} hplc.pdf"], "only the new file, not the one already there"
    sec = next(x for x in e["entries"] if x["title"] == instruments.SECTION_TITLE)
    assert sec["kind"] == "file" and sec["content"]["attachment_ids"] == [e["attachments"][0]["id"]]
    assert client.get(f"/api/attachments/{e['attachments'][0]['id']}", headers=h).content == data
    notes = client.get("/api/notifications", headers=h).json()["items"]
    assert any(n["kind"] == "instrument" and exp["eid"] in n["text"] for n in notes)
    # nothing more happens on later scans
    scan(400)
    assert len(get(client, h, exp)["attachments"]) == 1


def test_bruker_dataset_arrives_as_one_zip_matched_by_its_title(client, people, notebook, folder, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    add(folder, kind="bruker_nmr", name="NMR Bruker 400")
    scan(0)
    d = bruker(folder / "jl", "JL-013-20261021", 1, title=f"crude product {exp['eid']}\n")
    scan(10)
    scan(200)
    atts = get(client, h, exp)["attachments"]
    assert [a["filename"] for a in atts] == ["JL-013-20261021_1.zip"]
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/attachments/{atts[0]['id']}", headers=h).content))
    assert sorted(z.namelist()) == ["JL-013-20261021/1/acqus", "JL-013-20261021/1/fid", "JL-013-20261021/1/pdata/1/title"]
    assert z.read("JL-013-20261021/1/fid") == (d / "fid").read_bytes()


def test_a_dataset_changed_later_arrives_again(client, people, notebook, folder, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    add(folder, kind="bruker_nmr")
    scan(0)
    d = bruker(folder, exp["eid"], 1)
    scan(10)
    scan(200)
    assert len(get(client, h, exp)["attachments"]) == 1
    (d / "pdata" / "1" / "1r").write_bytes(b"processed later in TopSpin")
    scan(300)
    scan(500)
    atts = get(client, h, exp)["attachments"]
    assert len(atts) == 2 and atts[0]["sha256"] != atts[1]["sha256"]
    with Session(engine) as s:
        assert sorted(r.revision for r in s.exec(select(InstrumentFile).where(InstrumentFile.path == f"{exp['eid']}/1"))) == [1, 2]


def test_clutter_and_partial_downloads_never_arrive(client, people, admin, notebook, folder, add):
    exp = new_exp(client, people["alice"], notebook)
    add(folder)
    scan(0)
    for n in (".DS_Store", f"~${exp['eid']}.docx", f"{exp['eid']}.pdf.part", f"{exp['eid']}.crdownload", f".{exp['eid']}.pdf"):
        (folder / n).write_bytes(b"x")
    scan(10)
    scan(300)
    assert not get(client, people["alice"], exp)["attachments"]
    assert not any(exp["eid"] in i["path"] for i in inbox(client, admin))


def test_older_files_can_be_imported_from_a_date(client, people, notebook, folder, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    old, recent = folder / f"{exp['eid']} 2019.csv", folder / f"{exp['eid']} recent.csv"
    old.write_text("a,b\n1,2\n")
    recent.write_text("a,b\n3,4\n")
    os.utime(old, (datetime(2019, 5, 1).timestamp(),) * 2)
    since = datetime.now() - timedelta(days=2)
    add(folder, import_since=since.isoformat())
    scan(0)
    assert [a["filename"] for a in get(client, h, exp)["attachments"]] == [recent.name]


# ------------------------------------------------------------------ the Inbox and who sees it

def test_unmatched_files_wait_and_are_assigned_with_one_click(client, people, admin, notebook, folder, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    add(folder)
    scan(0)
    (folder / "jl13 forgot the number.jdx").write_text("##TITLE=x\n##END=\n")
    scan(10)
    scan(200)
    item = next(i for i in inbox(client, h) if i["path"] == "jl13 forgot the number.jdx")
    assert "No experiment number" in item["note"] and item["files"][0]["name"] == "jl13 forgot the number.jdx"
    assert client.get(f"/api/inbox/{item['id']}/files/0", headers=h).text.startswith("##TITLE")
    assert client.get("/api/inbox/count", headers=h).json()["waiting"] >= 1
    assert any(t["id"] == exp["id"] for t in client.get("/api/inbox/targets", headers=h).json())
    # a read-only account can see it but not assign it
    assert client.post(f"/api/inbox/{item['id']}/assign", headers=people["vic"], json={"experiment_id": exp["id"]}).status_code == 403
    # bob can't put it in alice's experiment
    assert client.post(f"/api/inbox/{item['id']}/assign", headers=people["bob"], json={"experiment_id": exp["id"]}).status_code == 403
    r = client.post(f"/api/inbox/{item['id']}/assign", headers=h, json={"experiment_id": exp["id"]})
    assert r.status_code == 200 and r.json()["state"] == "attached"
    assert [a["filename"] for a in get(client, h, exp)["attachments"]] == ["jl13 forgot the number.jdx"]
    assert not any(i["id"] == item["id"] for i in inbox(client, h))
    assert any(i["id"] == item["id"] for i in inbox(client, h, "attached"))
    # assigning again is refused: it's been dealt with
    assert client.post(f"/api/inbox/{item['id']}/assign", headers=h, json={"experiment_id": exp["id"]}).status_code == 409


def test_two_people_assigning_at_once_only_one_wins(client, people, notebook, folder, add):
    h = people["alice"]
    a, b = new_exp(client, h, notebook, "Race A"), new_exp(client, h, notebook, "Race B")
    add(folder)
    scan(0)
    (folder / "race.csv").write_text("x\n")
    scan(10)
    scan(200)
    item = next(i for i in inbox(client, h) if i["path"] == "race.csv")
    gate, out = threading.Barrier(2), [None, None]

    def go(n, exp):
        gate.wait()
        out[n] = client.post(f"/api/inbox/{item['id']}/assign", headers=h, json={"experiment_id": exp["id"]}).status_code
    ts = [threading.Thread(target=go, args=(0, a)), threading.Thread(target=go, args=(1, b))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == [200, 409], out
    assert len(get(client, h, a)["attachments"]) + len(get(client, h, b)["attachments"]) == 1


def test_folder_audience_limits_who_sees_unmatched_files(client, people, admin, folder, add):
    g = client.post("/api/groups", headers=admin, json={"name": "Instrument audience group"}).json()
    client.put(f"/api/groups/{g['id']}/members/{people['ids']['bob']}", headers=admin, json={})
    add(folder, audience=[g["id"]], name="Group NMR")
    scan(0)
    (folder / "secret compound name.pdf").write_bytes(b"%PDF")
    scan(10)
    scan(200)
    item = next(i for i in inbox(client, admin) if i["path"] == "secret compound name.pdf")
    assert any(i["id"] == item["id"] for i in inbox(client, people["bob"]))
    for outsider in ("alice", "carl"):
        hh = people[outsider]
        assert not any(i["id"] == item["id"] for i in inbox(client, hh))
        assert client.get(f"/api/inbox/{item['id']}/files/0", headers=hh).status_code == 404
        assert client.post(f"/api/inbox/{item['id']}/ignore", headers=hh).status_code == 404


def test_ignore_and_bring_back(client, people, folder, add):
    h = people["alice"]
    add(folder)
    scan(0)
    (folder / "blank run.csv").write_text("x\n")
    scan(10)
    scan(200)
    item = next(i for i in inbox(client, h) if i["path"] == "blank run.csv")
    assert client.post(f"/api/inbox/{item['id']}/ignore", headers=h).json()["state"] == "ignored"
    assert not any(i["id"] == item["id"] for i in inbox(client, h))
    assert any(i["id"] == item["id"] for i in inbox(client, h, "ignored"))
    assert client.post(f"/api/inbox/{item['id']}/unignore", headers=h).json()["state"] == "inbox"


def test_two_numbers_or_a_missing_one_wait_with_a_reason(client, people, notebook, folder, add):
    h = people["alice"]
    a, b = new_exp(client, h, notebook), new_exp(client, h, notebook)
    add(folder)
    scan(0)
    (folder / f"{a['eid']} vs {b['eid']}.csv").write_text("x\n")
    (folder / "EXP-99999 typo.csv").write_text("y\n")
    scan(10)
    scan(200)
    items = {i["path"]: i for i in inbox(client, h)}
    both = items[f"{a['eid']} vs {b['eid']}.csv"]
    assert "more than one experiment" in both["note"] and {m["eid"] for m in both["matched"]} == {a["eid"], b["eid"]}
    assert "no experiment EXP-99999" in items["EXP-99999 typo.csv"]["note"]
    assert not get(client, h, a)["attachments"] and not get(client, h, b)["attachments"]


# ------------------------------------------------------------------ signed records and follow-ups

def test_a_signed_record_is_never_changed_and_gets_a_follow_up(client, people, notebook, folder, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook, "Signed before the HRMS came back")
    client.put(f"/api/experiments/{exp['id']}/shares/{people['ids']['bob']}", headers=h, json={"permission": "view"})
    sign(client, h, exp)
    before = client.get(f"/api/experiments/{exp['id']}/verify", headers=h).json()
    add(folder)
    scan(0)
    (folder / f"{exp['eid']} HRMS.pdf").write_bytes(b"%PDF hrms")
    scan(10)
    scan(200)
    assert not get(client, h, exp)["attachments"]
    item = next(i for i in inbox(client, h) if i["path"] == f"{exp['eid']} HRMS.pdf")
    assert "already signed" in item["note"] and item["follow_up"]
    assert any(n["kind"] == "inbox" for n in client.get("/api/notifications", headers=h).json()["items"])
    r = client.post(f"/api/inbox/{item['id']}/follow-up", headers=h, json={})
    assert r.status_code == 201, r.text
    fu = client.get(f"/api/experiments/{r.json()['id']}", headers=h).json()
    assert fu["title"].startswith(f"Follow-up to {exp['eid']}") and fu["status"] == "open"
    assert fu["follows"]["eid"] == exp["eid"] and [a["filename"] for a in fu["attachments"]] == [f"{exp['eid']} HRMS.pdf"]
    orig = get(client, h, exp)
    assert [f["eid"] for f in orig["follow_ups"]] == [fu["eid"]]
    # the signed record is exactly as it was
    after = client.get(f"/api/experiments/{exp['id']}/verify", headers=h).json()
    assert after["content_hash"] == before["content_hash"] and after["intact"]
    # bob could see the original, so he can see the follow-up
    assert client.get(f"/api/experiments/{fu['id']}", headers=people["bob"]).status_code == 200


def test_follow_up_links_only_name_what_you_can_see(client, people, admin, folder, add):
    a = people["alice"]
    private = client.post("/api/notebooks", headers=a, json={"name": "Alice private", "visibility": "private"}).json()
    exp = new_exp(client, a, private, "Private signed work")
    sign(client, a, exp)
    # carl can't see it, so he can't make a follow-up of it
    assert client.post(f"/api/experiments/{exp['id']}/follow-up", headers=people["carl"], json={}).status_code == 404
    public = client.post("/api/notebooks", headers=a, json={"name": "Alice public"}).json()
    fu = client.post(f"/api/experiments/{exp['id']}/follow-up", headers=a, json={"notebook_id": public["id"], "copy_shares": False})
    assert fu.status_code == 201
    seen_by_carl = client.get(f"/api/experiments/{fu.json()['id']}", headers=people["carl"]).json()
    assert seen_by_carl["follows"] == {"hidden": True}
    # a draft doesn't get follow-ups
    draft = new_exp(client, a, public)
    assert client.post(f"/api/experiments/{draft['id']}/follow-up", headers=a, json={}).status_code == 409


def test_signed_private_match_is_only_visible_to_its_readers(client, people, folder, add):
    a = people["alice"]
    private = client.post("/api/notebooks", headers=a, json={"name": "Alice private 2", "visibility": "private"}).json()
    exp = new_exp(client, a, private, "Private and signed")
    sign(client, a, exp)
    add(folder)
    scan(0)
    (folder / f"{exp['eid']} late result.pdf").write_bytes(b"%PDF")
    scan(10)
    scan(200)
    item = next(i for i in inbox(client, a) if exp["eid"] in i["path"])
    for other in ("bob", "carl"):
        assert not any(i["id"] == item["id"] for i in inbox(client, people[other]))
        assert client.post(f"/api/inbox/{item['id']}/follow-up", headers=people[other], json={}).status_code == 404


def test_signed_between_deciding_and_attaching(client, people, notebook, folder, add, monkeypatch):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    sign(client, h, exp)
    real = instruments.decide

    def stale(session, rel, extra=""):  # as if the decision was made a moment before the signature
        from app.models import Experiment
        return instruments.Decision("attach", experiment=session.get(Experiment, exp["id"]), matched_ids=[exp["id"]], rule="eid")
    add(folder)
    scan(0)
    (folder / "race.csv").write_text("x\n")
    scan(10)
    monkeypatch.setattr(instruments, "decide", stale)
    scan(200)
    monkeypatch.setattr(instruments, "decide", real)
    assert not get(client, h, exp)["attachments"]
    item = next(i for i in inbox(client, h) if i["path"] == "race.csv")
    assert "signed just before" in item["note"]


# ------------------------------------------------------------------ "Not mine"

def test_not_mine_sends_a_file_back_and_out_of_the_record(client, people, admin, notebook, folder, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook, "Got someone else's spectrum")
    add(folder)
    scan(0)
    (folder / f"{exp['eid']} typo.jdx").write_text("##TITLE=not mine\n##END=\n")
    scan(10)
    scan(200)
    att = get(client, h, exp)["attachments"][0]
    assert client.post(f"/api/attachments/{att['id']}/not-mine", headers=people["bob"]).status_code == 403
    r = client.post(f"/api/attachments/{att['id']}/not-mine", headers=h)
    assert r.status_code == 200, r.text
    e = get(client, h, exp)
    assert not e["attachments"]
    assert next(x for x in e["entries"] if x["title"] == instruments.SECTION_TITLE)["content"]["attachment_ids"] == []
    assert client.get(f"/api/attachments/{att['id']}", headers=h).status_code == 404
    item = next(i for i in inbox(client, people["carl"]) if i["path"] == f"{exp['eid']} typo.jdx")  # back to everyone
    assert "wasn't theirs" in item["note"]
    # signing now covers the record without it, and the record verifies
    sign(client, h, exp)
    assert client.get(f"/api/experiments/{exp['id']}/verify", headers=h).json()["intact"]
    assert check.check_live()["ok"]


def test_not_mine_only_for_instrument_files_on_drafts(client, people, notebook, folder, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    up = client.post(f"/api/experiments/{exp['id']}/attachments", headers=h,
                     files={"file": ("mine.txt", io.BytesIO(b"uploaded by hand"), "text/plain")}).json()
    assert client.post(f"/api/attachments/{up['id']}/not-mine", headers=h).status_code == 400
    add(folder)
    scan(0)
    (folder / f"{exp['eid']}.csv").write_text("x\n")
    scan(10)
    scan(200)
    att = next(a for a in get(client, h, exp)["attachments"] if a["filename"] == f"{exp['eid']}.csv")
    sign(client, h, exp)
    assert client.post(f"/api/attachments/{att['id']}/not-mine", headers=h).status_code == 409


# ------------------------------------------------------------------ admin, limits and problems

def test_checking_a_folder_changes_nothing(client, admin, people, notebook, folder):
    exp = new_exp(client, people["alice"], notebook)
    (folder / f"{exp['eid']}.csv").write_text("x\n")
    (folder / "unknown.csv").write_text("y\n")
    r = client.post("/api/instruments/check", headers=admin, json={"folder": str(folder), "kind": "files",
                                                                   "import_since": "2000-01-01T00:00:00Z"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["items"] == 2 and {x["action"] for x in p["latest"]} == {"attach", "inbox"}
    assert p["since"] == {"items": 2, "attach": 1, "inbox": 1}
    assert not get(client, people["alice"], exp)["attachments"]
    assert client.post("/api/instruments/check", headers=people["alice"], json={"folder": str(folder)}).status_code == 403
    for bad, words in (("relative/path", "full path"), (str(folder / "nope"), "isn't there"), (str(settings.data_dir), "outside")):
        r = client.post("/api/instruments/check", headers=admin, json={"folder": bad, "kind": "files"})
        assert r.status_code == 400 and words in r.json()["detail"], r.text


def test_same_folder_twice_is_refused(client, admin, folder, add):
    add(folder)
    r = client.post("/api/instruments", headers=admin, json={"name": "Again", "kind": "files", "folder": str(folder)})
    assert r.status_code == 409


def test_too_large_waits_without_copying(client, people, notebook, folder, add, monkeypatch):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    monkeypatch.setattr(settings, "instrument_max_mb", 0)
    add(folder)
    scan(0)
    (folder / f"{exp['eid']} huge.raw").write_bytes(b"x" * 1000)
    scan(10)
    scan(200)
    item = next(i for i in inbox(client, h) if i["path"].endswith("huge.raw"))
    assert "limit" in item["note"] and item["files"] == []
    assert client.post(f"/api/inbox/{item['id']}/assign", headers=h, json={"experiment_id": exp["id"]}).status_code == 409


def test_a_missing_folder_is_reported_and_others_carry_on(client, admin, people, notebook, tmp_path, add):
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    gone, ok = tmp_path / "unplugged", tmp_path / "fine"
    gone.mkdir()
    ok.mkdir()
    a = add(gone, name="Unplugged drive")
    add(ok, name="Fine drive")
    scan(0)
    gone.rmdir()
    (ok / f"{exp['eid']}.csv").write_text("x\n")
    scan(10)
    scan(200)
    insts = {i["id"]: i for i in client.get("/api/instruments", headers=admin).json()["instruments"]}
    assert "isn't there" in insts[a["id"]]["last_error"]
    assert get(client, h, exp)["attachments"]


def test_the_instruments_account_isnt_a_person(client, admin, people, notebook, folder, add):
    exp = new_exp(client, people["alice"], notebook)
    add(folder)
    scan(0)
    (folder / f"{exp['eid']}.csv").write_text("x\n")
    scan(10)
    scan(200)
    assert instruments.SYSTEM_USERNAME not in [u["username"] for u in client.get("/api/users", headers=admin).json()]
    with Session(engine) as s:
        u = s.exec(select(User).where(User.username == instruments.SYSTEM_USERNAME)).one()
        assert not u.active
    assert client.post("/api/auth/login", json={"username": instruments.SYSTEM_USERNAME, "password": "!disabled"}).status_code == 401


def test_links_in_a_watched_folder_are_never_followed(client, people, notebook, folder, add, tmp_path):
    """Someone who can write to the share mustn't make Benchtop copy files from elsewhere (its secret key)."""
    h = people["alice"]
    exp = new_exp(client, h, notebook)
    secret = settings.data_dir / ".secret_key"
    add(folder, kind="bruker_nmr")
    scan(0)
    d = bruker(folder, exp["eid"], 1)
    (d / "stolen").symlink_to(secret)
    (folder / exp["eid"] / "2").mkdir()
    (folder / exp["eid"] / "2" / "acqus").symlink_to(secret)  # a fake dataset made of a link
    files = tmp_path / "files-instrument"
    files.mkdir()
    (files / f"{exp['eid']} key.txt").symlink_to(secret)
    add(files, name="Files with a link")
    scan(5)
    scan(10)
    scan(300)
    atts = get(client, h, exp)["attachments"]
    assert [a["filename"] for a in atts] == [f"{exp['eid']}_1.zip"]
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/attachments/{atts[0]['id']}", headers=h).content))
    assert not any(n.endswith("stolen") for n in z.namelist())


def test_an_assigned_file_follows_its_records_sharing(client, people, folder, add):
    a = people["alice"]
    private = client.post("/api/notebooks", headers=a, json={"name": "Alice private 3", "visibility": "private"}).json()
    exp = new_exp(client, a, private, "Private target")
    add(folder)
    scan(0)
    (folder / "unlabelled.pdf").write_bytes(b"%PDF private now")
    scan(10)
    scan(200)
    item = next(i for i in inbox(client, people["carl"]) if i["path"] == "unlabelled.pdf")  # everyone sees it waiting
    assert client.post(f"/api/inbox/{item['id']}/assign", headers=a, json={"experiment_id": exp["id"]}).status_code == 200
    assert client.get(f"/api/inbox/{item['id']}/files/0", headers=people["carl"]).status_code == 404
    assert not any(i["id"] == item["id"] for i in inbox(client, people["carl"], "attached"))
    assert client.get(f"/api/inbox/{item['id']}/files/0", headers=a).status_code == 200
