import io

import pytest
from sqlmodel import Session

from app.db import engine
from app.models import AuditEvent, Entry


def new_exp(client, h, nb, **kw):
    r = client.post("/api/experiments", headers=h, json={"title": "Test exp", "notebook_id": nb["id"], **kw})
    assert r.status_code == 201, r.text
    return client.get(f"/api/experiments/{r.json()['id']}", headers=h).json()


def test_login_and_me(client, users):
    assert client.get("/api/auth/me", headers=users["alice"]).json()["username"] == "alice"
    assert client.post("/api/auth/login", json={"username": "alice", "password": "nope"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage.sig"}).status_code == 401


def test_viewer_is_read_only(client, users, notebook):
    assert client.post("/api/notebooks", headers=users["vic"], json={"name": "x"}).status_code == 403
    assert client.get("/api/notebooks", headers=users["vic"]).status_code == 200


def test_default_sections_and_versioning(client, users, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    assert [e["kind"] for e in exp["entries"]] == ["text", "reaction", "safety", "text", "purification", "characterization", "text"]
    eid = exp["entries"][0]["id"]
    r = client.put(f"/api/entries/{eid}", headers=h, json={"content": {"html": "<p>hi<script>x</script></p>"},
                                                            "expected_version": 1})
    assert r.json()["version"] == 2 and r.json()["content"]["html"] == "<p>hi</p>"
    # stale write is rejected
    assert client.put(f"/api/entries/{eid}", headers=h, json={"content": {"html": "x"},
                                                              "expected_version": 1}).status_code == 409
    vs = client.get(f"/api/entries/{eid}/versions", headers=h).json()
    assert [v["version"] for v in vs] == [2, 1]
    # only owner edits
    assert client.put(f"/api/entries/{eid}", headers=users["bob"], json={"title": "x"}).status_code == 403


def test_reaction_entry_computes_stoichiometry(client, users, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    rxn = exp["entries"][1]
    rows = [{"name": "ArBr", "smiles": "COc1ccc(Br)cc1", "basis": "mass", "mass_mg": 187.036, "limiting": True},
            {"name": "B", "smiles": "OB(O)c1ccccc1", "equiv": 1.5},
            {"role": "product", "smiles": "COc1ccc(-c2ccccc2)cc1", "actual_mass_mg": 92.119}]
    out = client.put(f"/api/entries/{rxn['id']}", headers=h, json={"content": {"rows": rows}}).json()
    r = out["content"]["rows"]
    assert r[0]["mmol"] == pytest.approx(1.0, abs=1e-3)
    assert r[1]["mmol"] == pytest.approx(1.5, abs=1e-3)
    assert r[1]["mass_mg"] == pytest.approx(1.5 * 121.932, rel=1e-3)
    assert r[2]["yield_pct"] == pytest.approx(50.0, abs=0.1)


def test_parse_reaction(client, users):
    r = client.post("/api/chem/reaction/parse", headers=users["alice"],
                    json={"reaction": "CC(=O)O.OCC>[H+]>CC(=O)OCC"}).json()
    assert [x["role"] for x in r["rows"]] == ["reactant", "reactant", "reagent", "product"]
    assert r["rows"][0]["limiting"]


def test_sign_witness_lock_and_tamper(client, users, notebook):
    a, b = users["alice"], users["bob"]
    exp = new_exp(client, a, notebook)
    eid = exp["id"]
    assert client.post(f"/api/experiments/{eid}/sign", headers=a, json={"password": "wrong"}).status_code == 401
    assert client.post(f"/api/experiments/{eid}/sign", headers=b, json={"password": "lab-bench-pw-2026"}).status_code == 403
    r = client.post(f"/api/experiments/{eid}/sign", headers=a, json={"password": "lab-bench-pw-2026", "comment": "done"})
    assert r.status_code == 200 and r.json()["status"] == "signed"
    # locked
    entry_id = exp["entries"][0]["id"]
    assert client.put(f"/api/entries/{entry_id}", headers=a, json={"title": "x"}).status_code == 409
    # author cannot witness
    assert client.post(f"/api/experiments/{eid}/witness", headers=a, json={"password": "lab-bench-pw-2026"}).status_code == 403
    assert client.get(f"/api/experiments/{eid}/verify", headers=a).json()["intact"]

    # simulate out-of-band DB tampering
    with Session(engine) as s:
        e = s.get(Entry, entry_id)
        e.content = {"html": "<p>forged</p>"}
        s.add(e)
        s.commit()
    assert not client.get(f"/api/experiments/{eid}/verify", headers=a).json()["intact"]
    assert client.post(f"/api/experiments/{eid}/witness", headers=b, json={"password": "lab-bench-pw-2026"}).status_code == 409


def test_witness_and_reject_flow(client, users, notebook):
    a, b = users["alice"], users["bob"]
    exp = new_exp(client, a, notebook)
    eid = exp["id"]
    client.post(f"/api/experiments/{eid}/sign", headers=a, json={"password": "lab-bench-pw-2026"})
    assert client.post(f"/api/experiments/{eid}/reject", headers=b, json={"password": "lab-bench-pw-2026"}).status_code == 400
    r = client.post(f"/api/experiments/{eid}/reject", headers=b, json={"password": "lab-bench-pw-2026", "comment": "add yield"})
    assert r.json()["status"] == "open"
    client.post(f"/api/experiments/{eid}/sign", headers=a, json={"password": "lab-bench-pw-2026"})
    r = client.post(f"/api/experiments/{eid}/witness", headers=b, json={"password": "lab-bench-pw-2026"})
    assert r.json()["status"] == "witnessed"
    assert [s["meaning"] for s in r.json()["signatures"]] == ["author", "rejected", "author", "witness"]
    html = client.get(f"/api/experiments/{eid}/export", headers=a).text
    assert "Record content hash" in html and "witness" in html


def test_templates_and_clone(client, users, notebook):
    a = users["alice"]
    tpl = client.post("/api/experiments", headers=a, json={"title": "T", "is_template": True}).json()
    assert tpl["eid"].startswith("TPL-")
    full = client.get(f"/api/experiments/{tpl['id']}", headers=a).json()
    client.put(f"/api/entries/{full['entries'][0]['id']}", headers=a, json={"content": {"html": "<p>tpl aim</p>"}})
    exp = new_exp(client, a, notebook, template_id=tpl["id"])
    assert exp["entries"][0]["content"]["html"] == "<p>tpl aim</p>"
    cl = client.post(f"/api/experiments/{exp['id']}/clone", headers=a).json()
    assert cl["title"].startswith("Copy of")


def test_attachments(client, users, notebook):
    a = users["alice"]
    exp = new_exp(client, a, notebook)
    r = client.post(f"/api/experiments/{exp['id']}/attachments", headers=a,
                    files={"file": ("nmr.txt", io.BytesIO(b"1H NMR data"), "text/plain")})
    assert r.status_code == 201
    att = r.json()
    d = client.get(f"/api/attachments/{att['id']}", headers=a)
    assert d.content == b"1H NMR data"


def test_materials_inventory(client, users, notebook):
    a = users["alice"]
    m = client.post("/api/materials", headers=a, json={"name": "Ethanol", "smiles": "CCO", "density": 0.789,
                                                       "kind": "solvent"}).json()
    assert m["formula"] == "C2H6O" and m["mid"].startswith("MAT-")
    assert client.post("/api/materials", headers=a, json={"name": "EtOH", "smiles": "OCC"}).status_code == 409
    lot = client.post(f"/api/materials/{m['id']}/lots", headers=a, json={"amount": 100, "unit": "mL"}).json()
    # consume 7.89 g -> 10 mL via density
    r = client.post(f"/api/lots/{lot['id']}/transactions", headers=a, json={"amount": 7.89, "unit": "g"})
    assert r.json()["lot"]["amount"] == pytest.approx(90.0)
    assert client.post(f"/api/lots/{lot['id']}/transactions", headers=a,
                       json={"amount": 1, "unit": "L"}).status_code == 409
    hist = client.get(f"/api/lots/{lot['id']}/transactions", headers=a).json()
    assert len(hist) == 2


def test_register_product(client, users, notebook):
    a = users["alice"]
    exp = new_exp(client, a, notebook)
    r = client.post("/api/materials/register-product", headers=a, json={
        "experiment_id": exp["id"], "name": "Biphenyl", "smiles": "c1ccc(-c2ccccc2)cc1", "amount_mg": 120}).json()
    assert r["material"]["kind"] == "product" and r["lot"]["amount"] == 120


def test_search_modes(client, users, notebook):
    a = users["alice"]
    client.post("/api/materials", headers=a, json={"name": "Toluene", "smiles": "Cc1ccccc1"})
    exp = new_exp(client, a, notebook, title="Grignard on chlorobenzene")
    client.put(f"/api/entries/{exp['entries'][1]['id']}", headers=a, json={"content": {"rows": [
        {"name": "PhCl", "smiles": "Clc1ccccc1", "basis": "mass", "mass_mg": 100, "limiting": True}]}})
    t = client.get("/api/search", headers=a, params={"q": "grignard"}).json()
    assert any(e["id"] == exp["id"] for e in t["experiments"])
    s = client.get("/api/search", headers=a, params={"q": "c1ccccc1Cl", "mode": "substructure"}).json()
    assert any(e["id"] == exp["id"] for e in s["experiments"])
    sim = client.get("/api/search", headers=a, params={"q": "Cc1ccccc1", "mode": "similarity", "threshold": 0.9}).json()
    assert sim["materials"][0]["name"] == "Toluene"


def test_audit_chain(client, users, admin):
    assert client.get("/api/audit", headers=users["alice"]).status_code == 403
    v = client.get("/api/audit/verify", headers=admin).json()
    assert v["ok"] and v["checked"] > 10
    with Session(engine) as s:
        ev = s.get(AuditEvent, 3)
        original = dict(ev.details)
        ev.details = {**ev.details, "forged": True}
        s.add(ev)
        s.commit()
    v = client.get("/api/audit/verify", headers=admin).json()
    assert not v["ok"] and v["broken_at"] == 3
    with Session(engine) as s:  # put it back so later tests see an intact chain
        ev = s.get(AuditEvent, 3)
        ev.details = original
        s.add(ev)
        s.commit()
    assert client.get("/api/audit/verify", headers=admin).json()["ok"]


def test_parse_reaction_keeps_salts_together(client, users):
    r = client.post("/api/chem/reaction/parse", headers=users["alice"],
                    json={"reaction": "O=Cc1ccccc1.[Na+].[BH4-]>>OCc1ccccc1"}).json()
    assert [x["formula"] for x in r["rows"]] == ["C7H6O", "H4BNa", "C7H8O"]
