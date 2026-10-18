"""v0.12.1: regressions for what the October stress test found."""
import json
import threading

from sqlmodel import Session

from app import webhooks
from app.db import engine
from app.models import Entry, EntryKind

PW = "lab-bench-pw-2026"


def _exp(client, h, nb, title="Stress v12.1"):
    r = client.post("/api/experiments", headers=h, json={"title": title, "notebook_id": nb["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def _together(n, fn):
    """Run fn(i) in n threads released at the same moment; return the results."""
    gate, out = threading.Barrier(n), [None] * n

    def run(i):
        gate.wait()
        out[i] = fn(i)
    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def test_simultaneous_signs_store_one_signature(client, users, notebook):
    exp = _exp(client, users["alice"], notebook)
    codes = _together(6, lambda i: client.post(f"/api/experiments/{exp['id']}/sign", headers=users["alice"], json={"password": PW}).status_code)
    assert sorted(codes) == [200] + [409] * 5, codes
    sigs = client.get(f"/api/experiments/{exp['id']}/verify", headers=users["alice"]).json()["signatures"]
    assert [s["meaning"] for s in sigs] == ["author"]


def test_simultaneous_witness_and_reject_only_one_wins(client, users, notebook, admin):
    exp = _exp(client, users["alice"], notebook)
    assert client.post(f"/api/experiments/{exp['id']}/sign", headers=users["alice"], json={"password": PW}).status_code == 200

    def act(i):
        if i % 2:
            return client.post(f"/api/experiments/{exp['id']}/witness", headers=users["bob"], json={"password": PW}).status_code
        return client.post(f"/api/experiments/{exp['id']}/reject", headers=users["bob"], json={"password": PW, "comment": "redo"}).status_code
    codes = _together(6, act)
    assert codes.count(200) == 1 and codes.count(409) == 5, codes
    sigs = client.get(f"/api/experiments/{exp['id']}/verify", headers=users["alice"]).json()["signatures"]
    assert len(sigs) == 2  # author + exactly one of witness/rejected
    status = client.get(f"/api/experiments/{exp['id']}", headers=users["alice"]).json()["status"]
    assert status == ("witnessed" if sigs[-1]["meaning"] == "witness" else "open")


def test_simultaneous_voids_store_one(client, users, notebook):
    exp = _exp(client, users["alice"], notebook)
    codes = _together(4, lambda i: client.post(f"/api/experiments/{exp['id']}/void", headers=users["alice"], json={"password": PW, "comment": "dup"}).status_code)
    assert sorted(codes) == [200, 409, 409, 409], codes


def test_webhooks_dont_hold_extra_connections(client, users, notebook, monkeypatch):
    """Event dispatch used to open a second database connection while the request still held one;
    under load the pool ran dry. Now after-commit only queues work."""
    seen = []
    monkeypatch.setattr(webhooks, "synchronous", False)
    monkeypatch.setattr(webhooks, "_dispatch", lambda *a: seen.append(a))
    q = []
    monkeypatch.setattr(webhooks._q, "put", lambda item: q.append(item))
    _exp(client, users["alice"], notebook, "queued event")
    assert q and all(kind == "event" for kind, _ in q)
    assert not seen  # nothing ran inside the request


def test_malformed_sections_are_normalised(client, users, notebook):
    h = users["alice"]
    exp = _exp(client, h, notebook)
    bad = [
        ("reaction", {"rows": [{"mmol": "lots", "mw": -1, "equiv": "nan", "smiles": "(((", "name": {"x": 1}}, 5, None, "x"], "conditions": "hot"}),
        ("reaction", {"rows": "abc"}),
        ("characterization", {"items": [None, 1, "x", {"technique": 5, "data": {"a": [1]}, "attachment_id": "7"}]}),
        ("purification", {"isolated_mg": "abc", "eluent": 5, "method": ["x"]}),
        ("table", {"columns": "x", "rows": {"a": 1}}),
        ("table", {"columns": [{"name": 3}, "bad"], "rows": [[1, {"x": 2}, None], "row"]}),
        ("file", {"attachment_ids": "12", "analyses": [1, 2]}),
    ]
    for kind, content in bad:
        r = client.post(f"/api/experiments/{exp['id']}/entries", headers=h, json={"kind": kind, "content": content})
        assert r.status_code == 201, (kind, r.text)
    got = {e["kind"]: e["content"] for e in client.get(f"/api/experiments/{exp['id']}", headers=h).json()["entries"]}
    assert got["characterization"]["items"] == [{"technique": "5", "conditions": "", "data": "", "attachment_id": None}]
    assert got["purification"]["eluent"] == "5" and got["purification"]["method"] == ""
    assert got["file"]["attachment_ids"] == []
    # and everything that reads sections still works
    for path in (f"/api/experiments/{exp['id']}/export", f"/api/experiments/{exp['id']}/export?format=md",
                 f"/api/experiments/{exp['id']}/export?format=pdf", "/api/search?q=stress", "/api/reactions", "/api/compounds", "/api/dashboard"):
        assert client.get(path, headers=h).status_code == 200, path


def test_one_broken_old_record_doesnt_break_search_or_export(client, users, notebook):
    """Records stored by older versions could hold odd shapes; readers must cope."""
    h = users["alice"]
    exp = _exp(client, h, notebook, "legacy oddity zzlegacy")
    with Session(engine) as s:
        # shapes earlier versions really could store: odd values in condition/purification fields,
        # free-form tables and file lists (rows and items were always lists of objects)
        for kind, content in [(EntryKind.characterization, {"items": [{"technique": "NMR", "data": "zz"}]}),
                              (EntryKind.reaction, {"rows": [{"name": "A", "smiles": "CCO", "role": "reactant", "limiting": True}],
                                                    "conditions": {"atmosphere": 3, "temperature_label": ["x"], "notes": {"a": 1}}}),
                              (EntryKind.table, {"columns": [{"name": 1}, "x"], "rows": ["abc", [1, {"a": 2}]]}),
                              (EntryKind.purification, {"method": ["x"], "eluent": 5, "notes_html": None, "rf": {"a": 1}}),
                              (EntryKind.file, {"attachment_ids": "12", "analyses": [1, 2], "caption": 5})]:
            s.add(Entry(experiment_id=exp["id"], kind=kind, title=kind.value, content=content, position=10 + len(content)))
        s.commit()
    assert any(e["id"] == exp["id"] for e in client.get("/api/search?q=zzlegacy", headers=h).json()["experiments"])
    for mode in ("substructure", "similarity"):
        assert client.get(f"/api/search?q=c1ccccc1&mode={mode}", headers=h).status_code == 200
    for fmt in ("html", "md", "pdf"):
        assert client.get(f"/api/experiments/{exp['id']}/export?format={fmt}", headers=h).status_code == 200, fmt
    assert client.get("/api/reactions", headers=h).status_code == 200
    assert client.get("/api/dashboard", headers=h).status_code == 200


def test_similarity_threshold_edge_values(client, users):
    for t in ("0", "nan", "inf", "-1", "5"):
        assert client.get(f"/api/search?q=CCO&mode=similarity&threshold={t}", headers=users["alice"]).status_code == 200, t


def test_input_limits(client, users, notebook):
    h = users["alice"]
    nb = notebook["id"]
    assert client.post("/api/experiments", headers=h, json={"title": "x" * 301, "notebook_id": nb}).status_code == 422
    assert client.post("/api/experiments", headers=h, json={"title": "x", "notebook_id": nb, "tags": ["t"] * 51}).status_code == 422
    assert client.post("/api/experiments", headers=h, json={"title": "x", "notebook_id": nb, "tags": ["t" * 61]}).status_code == 422
    exp = _exp(client, h, notebook)
    assert client.post(f"/api/experiments/{exp['id']}/log", headers=h, json={"text": "x" * 5001}).status_code == 422
    deep = client.post(f"/api/experiments/{exp['id']}/entries", headers=h, json={"kind": "text", "content": {"html": "<b>" * 500 + "x"}})
    assert deep.status_code == 422
    ok = client.post(f"/api/experiments/{exp['id']}/entries", headers=h,
                     json={"kind": "text", "content": {"html": "<ul><li><p><b><i>nested list</i></b></p><ul><li><p>deeper</p></li></ul></li></ul>"}})
    assert ok.status_code == 201


def test_huge_ids_are_not_server_errors(client, users):
    h = users["alice"]
    assert client.get(f"/api/experiments/{10**20}", headers=h).status_code == 404
    assert client.post("/api/experiments", headers=h, json={"title": "x", "notebook_id": 10**20}).status_code in (404, 422)


def test_unknown_api_path_is_json_404(client, users):
    r = client.get("/api/definitely-not-a-route", headers=users["alice"])
    assert r.status_code == 404


def test_notebook_list_pages(client, users):
    h = users["alice"]
    nb = client.post("/api/notebooks", headers=h, json={"name": "Paging"}).json()
    for i in range(7):
        _exp(client, h, nb, f"page {i}")
    first = client.get(f"/api/experiments?notebook_id={nb['id']}&limit=5", headers=h).json()
    rest = client.get(f"/api/experiments?notebook_id={nb['id']}&limit=5&offset=5", headers=h).json()
    assert len(first) == 5 and len(rest) == 2
    assert not {e["id"] for e in first} & {e["id"] for e in rest}


def test_attachments_are_sandboxed(client, users, notebook):
    h = users["alice"]
    exp = _exp(client, h, notebook)
    att = client.post(f"/api/experiments/{exp['id']}/attachments", headers=h,
                      files={"file": ("x.html", b"<script>alert(1)</script>", "text/html")}).json()
    r = client.get(f"/api/attachments/{att['id']}?inline=true", headers=h)
    assert "sandbox" in r.headers["content-security-policy"]


def test_search_returns_capped_cards(client, users, notebook):
    h = users["alice"]
    for i in range(6):
        _exp(client, h, notebook, f"capword {i}")
    r = client.get("/api/search?q=capword&limit=3", headers=h).json()
    assert len(r["experiments"]) == 3 and all("owner" in e for e in r["experiments"])
    json.dumps(r)


def test_simultaneous_saves_of_one_version_only_one_wins(client, users, notebook):
    h = users["alice"]
    exp = _exp(client, h, notebook)
    en = client.post(f"/api/experiments/{exp['id']}/entries", headers=h, json={"kind": "text", "content": {"html": "<p>v1</p>"}}).json()
    codes = _together(10, lambda i: client.put(f"/api/entries/{en['id']}", headers=h,
                                                json={"content": {"html": f"<p>racer {i}</p>"}, "expected_version": en["version"]}).status_code)
    assert codes.count(200) == 1 and codes.count(409) == 9, codes
    versions = client.get(f"/api/entries/{en['id']}/versions", headers=h).json()
    nums = [v["version"] for v in versions]
    assert len(nums) == len(set(nums)), nums


def test_save_racing_a_signature_never_lands_after_it(client, users, notebook):
    h = users["alice"]
    for _ in range(3):
        exp = _exp(client, h, notebook)
        en = client.post(f"/api/experiments/{exp['id']}/entries", headers=h, json={"kind": "text", "content": {"html": "<p>a</p>"}}).json()

        def act(i):
            if i == 0:
                return client.post(f"/api/experiments/{exp['id']}/sign", headers=h, json={"password": PW}).status_code
            return client.put(f"/api/entries/{en['id']}", headers=h, json={"content": {"html": f"<p>edit {i}</p>"}}).status_code
        _together(6, act)
        v = client.get(f"/api/experiments/{exp['id']}/verify", headers=h).json()
        assert v["intact"], "an edit landed after the author signed"
