"""v0.11: bench log, restore a removed section, record links, usual reagents, stale drafts, PDF, phone hand-off."""
from datetime import timedelta

from sqlmodel import Session

from app.db import engine
from app.models import Experiment
from tests.test_v2 import new_exp

PW = "lab-bench-pw-2026"


def test_bench_log_appends_lines_and_photos(client, users, notebook):
    a = users["alice"]
    exp = new_exp(client, a, notebook)
    r = client.post(f"/api/experiments/{exp['id']}/log", headers=a, json={"text": "Added NaH <b>portionwise</b>", "time": "14:32", "day": "Sat 17 Oct 2026"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["title"] == "Bench log" and e["content"]["bench_log"]
    assert "<h3>Sat 17 Oct 2026</h3>" in e["content"]["html"] and "<strong>14:32</strong> Added NaH &lt;b&gt;portionwise" in e["content"]["html"]
    # same day: no second heading; it lands in the same section
    e2 = client.post(f"/api/experiments/{exp['id']}/log", headers=a, json={"text": "Gas evolution stopped", "time": "14:50", "day": "Sat 17 Oct 2026"}).json()
    assert e2["id"] == e["id"] and e2["content"]["html"].count("<h3>") == 1 and e2["version"] == e["version"] + 1
    # it sits before "Observations & results"
    titles = [x["title"] for x in client.get(f"/api/experiments/{exp['id']}", headers=a).json()["entries"]]
    if "Observations & results" in titles:
        assert titles.index("Bench log") < titles.index("Observations & results")
    # a photo: an attachment reference the sanitiser keeps
    att = client.post(f"/api/experiments/{exp['id']}/attachments", headers=a, files={"file": ("tlc.png", b"\x89PNG\r\n\x1a\nfake", "image/png")}).json()
    e3 = client.post(f"/api/experiments/{exp['id']}/log", headers=a, json={"text": "TLC after 1 h", "time": "15:32", "attachment_id": att["id"]}).json()
    assert f'data-attachment-id="{att["id"]}"' in e3["content"]["html"]
    # someone else can't log into alice's record; empty lines are refused
    assert client.post(f"/api/experiments/{exp['id']}/log", headers=users["bob"], json={"text": "x"}).status_code in (403, 404)
    assert client.post(f"/api/experiments/{exp['id']}/log", headers=a, json={"text": "  "}).status_code == 422
    # the HTML report carries the picture itself
    html = client.get(f"/api/experiments/{exp['id']}/export", headers=a).text
    assert "data:image/png;base64," in html and "TLC after 1 h" in html


def test_removed_sections_can_be_restored(client, users, notebook):
    a = users["alice"]
    exp = new_exp(client, a, notebook)
    full = client.get(f"/api/experiments/{exp['id']}", headers=a).json()
    victim = full["entries"][0]
    client.delete(f"/api/entries/{victim['id']}", headers=a)
    assert victim["id"] not in [e["id"] for e in client.get(f"/api/experiments/{exp['id']}", headers=a).json()["entries"]]
    r = client.post(f"/api/entries/{victim['id']}/restore", headers=a)
    assert r.status_code == 200
    back = client.get(f"/api/experiments/{exp['id']}", headers=a).json()["entries"]
    assert back[0]["id"] == victim["id"]  # in its old place
    assert any(ev["action"] == "restore" for ev in client.get(f"/api/experiments/{exp['id']}/audit", headers=a).json())


def test_record_links_respect_access(client, users, notebook):
    exp = new_exp(client, users["alice"], notebook)
    r = client.get(f"/api/lookup/{exp['eid']}", headers=users["bob"])
    assert r.status_code == 200, r.text
    r = r.json()
    assert r["kind"] == "experiment" and r["title"] == exp["title"]
    priv = client.post("/api/notebooks", headers=users["alice"], json={"name": "Mine", "visibility": "private"}).json()
    hidden = new_exp(client, users["alice"], priv)
    assert client.get(f"/api/lookup/{hidden['eid']}", headers=users["bob"]).status_code == 404
    assert client.get("/api/lookup/hello", headers=users["bob"]).status_code == 404


def test_usual_reagents(client, users, notebook):
    a = users["alice"]
    m = client.post("/api/materials", headers=a, json={"name": "Triethylamine", "smiles": "CCN(CC)CC", "density": 0.726}).json()
    for _ in range(2):
        exp = new_exp(client, a, notebook)
        rx = next(e for e in client.get(f"/api/experiments/{exp['id']}", headers=a).json()["entries"] if e["kind"] == "reaction")
        rx["content"]["rows"] = [{"role": "reagent", "name": "Triethylamine", "smiles": "CCN(CC)CC", "material_id": m["id"], "equiv": 2, "basis": "equiv"}]
        client.put(f"/api/entries/{rx['id']}", headers=a, json={"content": rx["content"]})
    top = client.get("/api/frequent-materials", headers=a).json()
    assert top[0]["id"] == m["id"] and top[0]["uses"] >= 2 and top[0]["role"] == "reagent"
    assert client.get("/api/frequent-materials", headers=users["vic"]).json() == []


def test_stale_drafts_on_dashboard(client, users, notebook):
    a = users["alice"]
    exp = new_exp(client, a, notebook)
    with Session(engine) as s:
        row = s.get(Experiment, exp["id"]); row.updated_at = row.updated_at - timedelta(days=20); s.add(row); s.commit()
    d = client.get("/api/dashboard", headers=a).json()
    assert exp["id"] in [e["id"] for e in d["lists"]["stale"]] and d["stale_days"] == 14


def test_pdf_export(client, users, notebook):
    a = users["alice"]
    exp = new_exp(client, a, notebook)
    rx = next(e for e in client.get(f"/api/experiments/{exp['id']}", headers=a).json()["entries"] if e["kind"] == "reaction")
    rx["content"]["reaction_smiles"] = "c1ccccc1Br.OB(O)c1ccccc1>>c1ccc(-c2ccccc2)cc1"
    client.put(f"/api/entries/{rx['id']}", headers=a, json={"content": rx["content"]})
    r = client.get(f"/api/experiments/{exp['id']}/export", headers=a, params={"format": "pdf"})
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-" and len(r.content) > 5000
    assert exp["eid"] in r.headers["content-disposition"]


def test_phone_handoff_is_single_use(client, users, notebook):
    a = users["alice"]
    exp = new_exp(client, a, notebook)
    link = client.post(f"/api/experiments/{exp['id']}/phone-link", headers=a).json()
    assert link["url"].endswith(f"/bench/{exp['id']}#handoff=" + link["url"].split("#handoff=")[1]) and "<svg" in link["svg"]
    assert link["reachable"] is True and link["expires_in"] == 300  # the test client's host is "testserver"
    local = client.post(f"/api/experiments/{exp['id']}/phone-link", headers={**a, "host": "localhost:8000"}).json()
    assert local["reachable"] is False and "./benchtop lab" in local["hint"]
    code = link["url"].split("#handoff=")[1]
    r = client.post("/api/auth/handoff", json={"code": code})
    assert r.status_code == 200 and r.json()["user"]["username"] == "alice" and r.json()["experiment_id"] == exp["id"]
    phone = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get(f"/api/experiments/{exp['id']}", headers=phone).status_code == 200
    assert any(s["method"] == "phone" for s in client.get("/api/auth/sessions", headers=phone).json())
    assert client.post("/api/auth/handoff", json={"code": code}).status_code == 401  # used up
    assert client.post("/api/auth/handoff", json={"code": "made-up"}).status_code == 401
