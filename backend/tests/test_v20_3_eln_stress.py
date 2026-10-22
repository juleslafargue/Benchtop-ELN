"""0.20.3: what stress-testing the .eln import and export found.

- a damaged or hand-edited record.benchtop.json (wrong types where Benchtop expects text or objects) is read as far as it
  makes sense; it used to stop the preview, or the import after records had already been saved, with a server error
- a date the calendar can't hold (year 9999 with a time zone) no longer makes the whole file refused
- a description nested thousands of levels deep is refused with a plain message instead of a server error
- a file type with a line break in it can't add headers to the download
- deeply nested HTML (in an import or a saved section) no longer ties the server up for minutes
- a large export (thousands of experiments) is read in seconds, not minutes
- the same upload imported twice at once (double click, two tabs, a retried request) creates the records once
- exported folder and file names fit every computer: at most 255 bytes (Chinese, Japanese, emoji), the extension kept,
  no names Windows reserves (CON, NUL…)
- a file damaged inside the .eln says so in plain words
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile

import pytest

from app import eln, eln_import
from app.routers.experiments import clean_html
from tests.test_v20_eln import _crate, _import, _preview, _simple, nb2  # noqa: F401  (fixture)


def _benchtop(record: dict, files: dict[str, bytes] | None = None) -> bytes:
    """An export that says it came from Benchtop, with this (possibly damaged) record.benchtop.json."""
    files = {"exp/record.benchtop.json": json.dumps({"format": "benchtop-record", "entries": [], **record}).encode(), **(files or {})}
    return _crate([{"@id": "./", "@type": "Dataset", "hasPart": [{"@id": "./exp/"}]},
                   {"@id": "./exp/", "@type": "Dataset", "name": "Damaged record", "hasPart": [{"@id": f"./{n}"} for n in files]},
                   *[{"@id": f"./{n}", "@type": "File", "name": n.split("/")[-1]} for n in files]], files)


CSV = b"x,y\n1,2\n"


@pytest.mark.parametrize("record", [
    {"experiment": "not an object"},
    {"experiment": [1, 2]},
    {"experiment": {"owner": "Ada"}},
    {"experiment": {"follows": ["EXP-00001"], "eid": "EXP-00002"}},
    {"experiment": {"eid": {"a": 1}, "status": ["signed"], "tags": "a, b"}},
    {"experiment": {}, "attachments": [{"sha256": hashlib.sha256(CSV).hexdigest(), "filename": 5, "content_type": 7}, "junk"]},
    {"experiment": {}, "comments": [{"body": "hi", "author": "Ada"}, {"body": ["x"]}, 3]},
    {"experiment": {}, "signatures": ["x", {"timestamp": "x", "meaning": 4}], "snapshot": {"attachments": [1, None]}},
])
def test_a_damaged_benchtop_record_still_imports(client, users, nb2, record):
    b = users["bob"]
    p, res = _import(client, b, _benchtop(record, {"exp/a.csv": CSV}), nb2)
    assert res["failed"] == [] and len(res["created"]) == 1, res
    e = client.get(f"/api/experiments/{res['created'][0]['id']}", headers=b).json()
    assert e["title"] == "Damaged record" and [a["filename"] for a in e["attachments"]][:1] in (["a.csv"], ["5"])


@pytest.mark.parametrize("when", ["9999-12-31T23:00:00-05:00", "0001-01-01T00:30:00+05:00"])
def test_a_date_out_of_the_calendar_does_not_refuse_the_file(client, users, nb2, when):
    p, res = _import(client, users["bob"], _simple(dateCreated=when), nb2)
    assert len(res["created"]) == 1
    e = client.get(f"/api/experiments/{res['created'][0]['id']}", headers=users["bob"]).json()
    assert e["origin"]["original_created"] == when  # kept as written


@pytest.mark.parametrize("meta", ["[" * 100_000 + "]" * 100_000, '{"@graph": ' + "[" * 50_000 + "]" * 50_000 + "}",
                                  '{"@graph": [{"@id": "./", "@type": "Dataset", "name": ' + "[" * 900 + "]" * 900 + "}]}"],
                         ids=["list", "graph", "title"])
def test_absurdly_nested_descriptions_are_refused_plainly(client, users, meta):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("export/ro-crate-metadata.json", meta)
    r = _preview(client, users["bob"], z.getvalue())
    assert r.status_code == 422 and "nested" in r.json()["detail"], r.text


def test_a_file_type_cannot_add_headers_to_the_download(client, users, nb2):
    b = users["bob"]
    data = _crate([{"@id": "./", "@type": "Dataset", "hasPart": [{"@id": "./exp/"}]},
                   {"@id": "./exp/", "@type": "Dataset", "name": "Header", "hasPart": [{"@id": "./exp/a.csv"}]},
                   {"@id": "./exp/a.csv", "@type": "File", "name": "a.csv", "encodingFormat": "text/csv\r\nX-Evil: 1"}],
                  {"exp/a.csv": CSV})
    _, res = _import(client, b, data, nb2)
    e = client.get(f"/api/experiments/{res['created'][0]['id']}", headers=b).json()
    a = next(a for a in e["attachments"] if a["filename"] == "a.csv")
    assert a["content_type"] == "application/octet-stream"
    r = client.get(f"/api/attachments/{a['id']}", headers=b)
    assert r.status_code == 200 and "x-evil" not in r.headers and "\n" not in r.headers["content-type"]


def test_a_bad_file_type_already_stored_downloads_safely(client, users, notebook):
    from sqlmodel import Session
    from app.db import engine
    from app.models import Attachment
    a = users["alice"]
    exp = client.post("/api/experiments", headers=a, json={"title": "Stored by 0.20.0", "notebook_id": notebook["id"]}).json()
    att = client.post(f"/api/experiments/{exp['id']}/attachments", headers=a, files={"file": ("a.csv", CSV, "text/csv")}).json()
    with Session(engine) as s:  # as an import by 0.20.0–0.20.2 could have stored it
        row = s.get(Attachment, att["id"])
        row.content_type = "text/csv\r\nX-Evil: 1"
        s.add(row)
        s.commit()
    r = client.get(f"/api/attachments/{att['id']}", headers=a)
    assert r.status_code == 200 and "x-evil" not in r.headers and r.headers["content-type"].startswith("application/octet-stream")


DEEP = "<div></x>" * 50_000  # each stray closing tag is ignored, so this is 50,000 levels deep


def test_deeply_nested_html_is_flattened_quickly():
    t = time.monotonic()
    out = clean_html(DEEP + "<p>kept text</p>")
    assert time.monotonic() - t < 3 and "<div>" not in out and "kept text" in out
    assert clean_html("<p>a<p>b<ul><li>c<li>d</ul>") .count("<p>") == 2  # ordinary HTML is untouched


def test_saving_a_deeply_nested_section_is_refused_quickly(client, users, notebook):
    a = users["alice"]
    exp = client.post("/api/experiments", headers=a, json={"title": "Deep", "notebook_id": notebook["id"]}).json()
    ent = next(e for e in client.get(f"/api/experiments/{exp['id']}", headers=a).json()["entries"] if e["kind"] == "text")
    t = time.monotonic()
    r = client.put(f"/api/entries/{ent['id']}", headers=a, json={"content": {"html": DEEP}, "expected_version": ent["version"]})
    assert r.status_code == 422 and time.monotonic() - t < 3, r.text


def test_importing_deeply_nested_html_keeps_the_text_quickly(client, users, nb2):
    t = time.monotonic()
    _, res = _import(client, users["bob"], _simple(DEEP + "<p>the procedure</p>"), nb2)
    assert time.monotonic() - t < 5
    e = client.get(f"/api/experiments/{res['created'][0]['id']}", headers=users["bob"]).json()
    assert "the procedure" in e["entries"][0]["content"]["html"]


def test_a_large_export_is_read_in_seconds(client, users):
    nodes, files = [{"@id": "./", "@type": "Dataset", "hasPart": [{"@id": f"./e{i}"} for i in range(6000)]}], {}
    for i in range(6000):
        parts = []
        for j in range(5):
            files[f"e{i}/f{j}.txt"] = b"x"
            parts.append({"@id": f"./e{i}/f{j}.txt"})
            nodes.append({"@id": f"./e{i}/f{j}.txt", "@type": "File", "name": f"f{j}.txt"})
        files[f"e{i}/unlisted.txt"] = b"y"  # in the folder but not in the description: still found
        nodes.append({"@id": f"./e{i}", "@type": "Dataset", "name": f"Run {i}", "hasPart": parts})
    data = _crate(nodes, files)
    t = time.monotonic()
    r = _preview(client, users["bob"], data)
    assert r.status_code == 200 and time.monotonic() - t < 10, f"{time.monotonic() - t:.1f}s"
    items = r.json()["items"]
    assert len(items) == 6000 and all(i["files"] == 6 for i in items)
    client.delete(f"/api/eln/import/{r.json()['token']}", headers=users["bob"])


def test_the_same_upload_imported_twice_at_once_is_imported_once(client, users, nb2, monkeypatch):
    b = users["bob"]
    p = _preview(client, b, _simple(name="Double click", url="https://elsewhere.example/7")).json()
    slow = eln_import._store

    def slow_store(crate, f):
        time.sleep(0.4)  # a big file: the second click lands while the first import is still copying
        return slow(crate, f)
    monkeypatch.setattr(eln_import, "_store", slow_store)
    out = []
    go = lambda: out.append(client.post(f"/api/eln/import/{p['token']}", headers=b, json={"notebook_id": nb2["id"], "items": ["0"]}))  # noqa: E731
    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    made = sum(len(r.json().get("created", [])) for r in out if r.status_code == 200)
    assert made == 1, [r.text for r in out]
    assert all(r.status_code == 200 or (r.status_code in (409, 422) and ("already" in r.text or "expired" in r.text)) for r in out)


def test_exported_names_fit_every_computer(client, users, notebook):
    a = users["alice"]
    exp = client.post("/api/experiments", headers=a, json={"title": "鈴木カップリング反応の最適化" * 6 + " 🧪" * 20,
                                                          "notebook_id": notebook["id"]}).json()
    names = ["化合物のNMRスペクトルデータ" * 8 + ".jdx", "CON.txt", "nul", "x" * 400 + ".pdf", "lpt1.csv"]
    for n in names:
        client.post(f"/api/experiments/{exp['id']}/attachments", headers=a, files={"file": (n, n.encode(), "text/plain")})
    r = client.get(f"/api/experiments/{exp['id']}/export.eln", headers=a)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    parts = [p for n in z.namelist() for p in n.split("/")]
    assert max(len(p.encode()) for p in parts) <= 255, max(parts, key=lambda p: len(p.encode()))
    stems = {p.split(".")[0].upper() for p in parts}
    assert not stems & {"CON", "NUL", "LPT1"}
    files = [n.rsplit("/", 1)[1] for n in z.namelist() if "/files/" in n]
    assert sum(f.endswith(".pdf") for f in files) == 1 and sum(f.endswith(".jdx") for f in files) == 1
    p = _preview(client, a, r.content).json()  # and it still imports
    assert p["items"][0]["files"] == len(names)
    client.delete(f"/api/eln/import/{p['token']}", headers=a)


def test_a_damaged_file_inside_says_so_plainly(client, users, nb2):
    data = bytearray(_simple())
    i = data.find(b"time,signal")
    data[i:i + 4] = b"TIME"  # the bytes change, the zip's checksum doesn't: as a bad download or disk would
    p, res = _import(client, users["bob"], bytes(data), nb2)
    assert res["created"] == [] and "damaged" in res["failed"][0]["error"] and "CRC" not in res["failed"][0]["error"]
    client.delete(f"/api/eln/import/{p['token']}", headers=users["bob"])


def test_titles_never_show_program_code(client, users, nb2):
    _, res = _import(client, users["bob"], _simple(name={"@value": {"x": 1}}), nb2)
    assert "{" not in res["created"][0]["title"]
