import io
import zipfile

import pytest

from app import backup
from app.config import settings


def new_exp(client, h, nb, **kw):
    r = client.post("/api/experiments", headers=h, json={"title": "V2 exp", "notebook_id": nb["id"], **kw})
    return client.get(f"/api/experiments/{r.json()['id']}", headers=h).json()


def test_default_sections_include_workup_and_characterisation(client, users, notebook):
    exp = new_exp(client, users["alice"], notebook)
    assert [e["kind"] for e in exp["entries"]] == ["text", "reaction", "text", "purification", "characterization", "text"]


def test_conditions_and_solvent_fill(client, users, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    rxn = next(e for e in exp["entries"] if e["kind"] == "reaction")
    out = client.put(f"/api/entries/{rxn['id']}", headers=h, json={"content": {
        "conditions": {"temperature_c": "65", "time_h": 2, "atmosphere": "Ar", "target_conc_m": 0.5, "bogus": 1},
        "rows": [{"name": "A", "smiles": "CCO", "basis": "mmol", "mmol": 2, "limiting": True},
                 {"role": "solvent", "name": "THF", "basis": "fill", "density": 0.889}]}}).json()
    c = out["content"]
    assert c["conditions"]["temperature_c"] == 65.0 and "bogus" not in c["conditions"]
    thf = c["rows"][1]
    assert thf["volume_ml"] == pytest.approx(4.0) and thf["mass_mg"] == pytest.approx(3556.0)
    assert c["totals"]["concentration_m"] == pytest.approx(0.5)


def test_purification_and_characterisation_sanitised(client, users, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    pur = next(e for e in exp["entries"] if e["kind"] == "purification")
    r = client.put(f"/api/entries/{pur['id']}", headers=h, json={"content": {
        "method": "column", "isolated_mg": "42.5", "notes_html": "<p>ok<script>x</script></p>"}}).json()
    assert r["content"]["isolated_mg"] == 42.5 and r["content"]["notes_html"] == "<p>ok</p>"
    ch = next(e for e in exp["entries"] if e["kind"] == "characterization")
    r = client.put(f"/api/entries/{ch['id']}", headers=h, json={"content": {"items": [
        {"technique": "HRMS", "data": "m/z 185.0961", "junk": True}]}}).json()
    assert r["content"]["items"] == [{"technique": "HRMS", "conditions": "", "data": "m/z 185.0961", "attachment_id": None}]
    html = client.get(f"/api/experiments/{exp['id']}/export", headers=h).text
    assert "Flash column chromatography" in html and "185.0961" in html


def test_reactions_overview_filters(client, users, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook, title="Overview probe")
    rxn = next(e for e in exp["entries"] if e["kind"] == "reaction")
    client.put(f"/api/entries/{rxn['id']}", headers=h, json={"content": {
        "conditions": {"temperature_c": 120, "atmosphere": "N2"},
        "rows": [{"name": "ArBr", "smiles": "Brc1ccccc1", "basis": "mass", "mass_mg": 157.0, "limiting": True},
                 {"role": "product", "smiles": "c1ccc(-c2ccccc2)cc1", "actual_mass_mg": 123.4}]}})
    all_ = client.get("/api/reactions", headers=h).json()
    me = next(r for r in all_ if r["experiment_id"] == exp["id"])
    assert me["yield_pct"] == pytest.approx(80.0, abs=0.2)
    hot = client.get("/api/reactions", headers=h, params={"min_temp": 100}).json()
    assert [r["experiment_id"] for r in hot] == [exp["id"]]
    sub = client.get("/api/reactions", headers=h, params={"substructure": "Brc1ccccc1", "min_yield": 79}).json()
    assert any(r["experiment_id"] == exp["id"] for r in sub)
    csv = client.get("/api/reactions", headers=h, params={"format": "csv", "q": "overview probe"}).text
    assert csv.count("\n") == 2


def test_backup_create_and_restore_roundtrip(client, users, admin, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook, title="Before backup")
    client.post(f"/api/experiments/{exp['id']}/attachments", headers=h,
                files={"file": ("spec.jdx", io.BytesIO(b"##TITLE=x\n##END=\n"), "text/plain")})
    assert client.post("/api/admin/backups", headers=h).status_code == 403
    b = client.post("/api/admin/backups", headers=admin).json()
    with zipfile.ZipFile(b["path"]) as z:
        names = z.namelist()
    assert "benchtop.db" in names and any(n.startswith("attachments/") for n in names)
    # change something after the backup, then restore and check it's gone
    client.post("/api/experiments", headers=h, json={"title": "After backup", "notebook_id": notebook["id"]})
    aside = backup.restore(b["path"])
    assert (aside / "benchtop.db").exists()
    titles = [e["title"] for e in client.get("/api/experiments", headers=h).json()]
    assert "Before backup" in titles and "After backup" not in titles


def test_backup_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "backup_dir", tmp_path)
    for i in range(5):
        (tmp_path / f"benchtop-backup-2026010{i}-000000.zip").write_bytes(b"x")
    backup.prune(keep=2)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "benchtop-backup-20260103-000000.zip", "benchtop-backup-20260104-000000.zip"]


def test_spectrum_analysis_is_saved_and_validated(client, users, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    ch = next(e for e in exp["entries"] if e["kind"] == "characterization")
    r = client.put(f"/api/entries/{ch['id']}", headers=h, json={"content": {"items": [
        {"technique": "1H NMR", "data": "", "analysis": {"integrals": [[7.6, 7.5], ["x", 1], [3.9, 3.8]],
                                                        "ref_idx": 1, "ref_value": "3", "threshold": 0.1, "evil": "<script>"}}]}}).json()
    a = r["content"]["items"][0]["analysis"]
    assert a == {"integrals": [[7.6, 7.5], [3.9, 3.8]], "ref_idx": 1, "ref_value": 3.0, "threshold": 0.1}


def test_writeup_draft(client, users, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook, title="Writeup probe")
    ents = {e["kind"]: e for e in exp["entries"]}
    w = client.get(f"/api/experiments/{exp['id']}/writeup", headers=h).json()
    assert "a reaction table with amounts" in w["missing"]
    client.put(f"/api/entries/{ents['reaction']['id']}", headers=h, json={"content": {
        "conditions": {"temperature_c": 25, "time_h": 0.5, "atmosphere": "Ar"},
        "rows": [{"name": "Benzaldehyde", "smiles": "O=Cc1ccccc1", "basis": "mass", "mass_mg": 106.12, "limiting": True, "density": 1.044},
                 {"role": "reagent", "name": "Sodium borohydride", "smiles": "[Na+].[BH4-]", "equiv": 1.5},
                 {"role": "solvent", "name": "Methanol", "basis": "volume", "volume_ml": 5},
                 {"role": "product", "name": "Benzyl alcohol", "smiles": "OCc1ccccc1"}]}})
    client.put(f"/api/entries/{ents['purification']['id']}", headers=h, json={"content": {
        "method": "column", "eluent": "20% EtOAc/hexanes", "isolated_mg": 97.3, "appearance": "Colourless oil", "rf": "0.3"}})
    client.put(f"/api/entries/{ents['characterization']['id']}", headers=h, json={"content": {"items": [
        {"technique": "1H NMR", "conditions": "400 MHz, CDCl3", "data": "δ 7.36–7.28 (m, 5H), 4.68 (s, 2H)"}]}})
    w = client.get(f"/api/experiments/{exp['id']}/writeup", headers=h).json()
    t = w["text"]
    assert t.startswith("Benzyl alcohol. Benzaldehyde (")
    assert "1.00 mmol" in t and "sodium borohydride" in t and "1.5 equiv" in t
    assert "in methanol (5.00 mL" in t and "at 25 °C under Ar for 30 min" in t
    assert "afforded benzyl alcohol as a colourless oil (97.3 mg, 90%)" in t
    assert "¹H NMR (400 MHz, CDCl3) δ 7.36" in t and w["missing"] == []
    assert "<sub>f</sub>" in w["html"] and "<script" not in w["html"]


def test_inventory_reversal(client, users):
    a = users["alice"]
    m = client.post("/api/materials", headers=a, json={"name": "Acetone", "smiles": "CC(C)=O", "density": 0.784}).json()
    lot = client.post(f"/api/materials/{m['id']}/lots", headers=a, json={"amount": 10, "unit": "mL"}).json()
    tx = client.post(f"/api/lots/{lot['id']}/transactions", headers=a, json={"amount": 10, "unit": "mL"}).json()
    assert tx["lot"]["status"] == "empty"
    r = client.post(f"/api/lots/transactions/{tx['transaction_id']}/reverse", headers=a)
    assert r.status_code == 201 and r.json()["lot"]["amount"] == pytest.approx(10) and r.json()["lot"]["status"] == "available"
    assert client.post(f"/api/lots/transactions/{tx['transaction_id']}/reverse", headers=a).status_code == 409
    assert client.post(f"/api/lots/transactions/{r.json()['transaction_id']}/reverse", headers=a).status_code == 409


def test_markdown_export_and_vault_sync(client, users, notebook, tmp_path):
    from sqlmodel import Session

    from app.db import engine
    from app.export_md import html_to_md, sync_vault

    assert html_to_md("<p>K<sub>2</sub>CO<sub>3</sub> <strong>dry</strong></p><ol><li>one</li><li>two</li></ol>") == \
        "K<sub>2</sub>CO<sub>3</sub> **dry**\n\n1. one\n2. two"
    h = users["alice"]
    exp = new_exp(client, h, notebook, title="Export probe", tags=["flow chem"])
    md = client.get(f"/api/experiments/{exp['id']}/export", headers=h, params={"format": "md"}).text
    assert md.startswith("---\ntype: \"experiment\"") and 'tags: ["flow-chem"]' in md and "# Export probe" in md
    vault = tmp_path / "Vault" / "Benchtop"
    with Session(engine) as s:
        r1 = sync_vault(s, vault)
        r2 = sync_vault(s, vault)
    assert r1["written"] >= 1 and r2["written"] == 0  # idempotent
    client.patch(f"/api/experiments/{exp['id']}", headers=h, json={"title": "Export probe renamed"})
    with Session(engine) as s:
        r3 = sync_vault(s, vault)
    names = [p.name for p in vault.rglob(f"{exp['eid']} *.md")]
    assert names == [f"{exp['eid']} Export probe renamed.md"] and r3["removed"] == 1
    assert (vault / "Benchtop index.md").exists()


def test_account_settings(client, admin):
    client.post("/api/users", headers=admin, json={"username": "carol", "password": "password123", "full_name": "Carol"})
    h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": "carol", "password": "password123"}).json()["token"]}
    r = client.patch("/api/auth/me", headers=h, json={"full_name": "Carol Diaz", "email": "carol@uni.edu",
                                                      "preferences": {"theme": "dark", "bogus": 1, "default_notebook_id": "3"}})
    assert r.status_code == 200 and r.json()["full_name"] == "Carol Diaz"
    assert r.json()["preferences"] == {"theme": "dark", "default_notebook_id": 3}
    assert client.patch("/api/auth/me", headers=h, json={"email": "not-an-email"}).status_code == 422
    assert client.patch("/api/auth/me", headers=h, json={"full_name": "  "}).status_code == 422
    assert client.post("/api/auth/username", headers=h, json={"new_username": "cdiaz", "password": "wrong"}).status_code == 400
    assert client.post("/api/auth/username", headers=h, json={"new_username": "alice", "password": "password123"}).status_code == 409
    assert client.post("/api/auth/username", headers=h, json={"new_username": "Bad Name!", "password": "password123"}).status_code == 422
    assert client.post("/api/auth/username", headers=h, json={"new_username": "CDiaz", "password": "password123"}).json()["username"] == "cdiaz"
    assert client.post("/api/auth/login", json={"username": "cdiaz", "password": "password123"}).status_code == 200
    # admin edits someone else
    uid = r.json()["id"]
    assert client.patch(f"/api/users/{uid}", headers=admin, json={"full_name": "Dr Carol Diaz", "email": "c@lab.org"}).json()["email"] == "c@lab.org"


def test_signature_keeps_name_at_signing(client, users, notebook):
    a = users["alice"]
    exp = new_exp(client, a, notebook, title="Name freeze")
    client.patch("/api/auth/me", headers=a, json={"full_name": "Alice Chen"})
    client.post(f"/api/experiments/{exp['id']}/sign", headers=a, json={"password": "password123"})
    client.patch("/api/auth/me", headers=a, json={"full_name": "Alice Chen-Okafor"})
    sig = client.get(f"/api/experiments/{exp['id']}", headers=a).json()["signatures"][0]
    assert sig["signer_name"] == "Alice Chen" and sig["user"]["full_name"] == "Alice Chen-Okafor"
    assert "Alice Chen<" in client.get(f"/api/experiments/{exp['id']}/export", headers=a).text


def test_spectrum_analysis_offset_and_labels(client, users, notebook):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    ch = next(e for e in exp["entries"] if e["kind"] == "characterization")
    r = client.put(f"/api/entries/{ch['id']}", headers=h, json={"content": {"items": [{"technique": "1H NMR", "analysis": {
        "integrals": [[7.6, 7.5], [3.9, 3.8]], "labels": [{"mult": "dd", "j": "8.4, 2.1"}], "offset": 0.012}}]}}).json()
    a = r["content"]["items"][0]["analysis"]
    assert a["offset"] == 0.012 and a["labels"] == [{"mult": "dd", "j": "8.4, 2.1"}, {"mult": "", "j": ""}]


def test_first_run_setup_on_fresh_database(tmp_path):
    """Run against a separate, empty database so the shared test DB isn't affected."""
    import os
    import subprocess
    import sys
    code = r'''
import os, sys
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as c:
    assert c.get("/api/setup").json() == {"needs_setup": True}
    assert c.post("/api/setup", json={"full_name": "", "username": "jules", "password": "longenough"}).status_code == 422
    assert c.post("/api/setup", json={"full_name": "Jules L", "username": "jules", "password": "short"}).status_code == 422
    r = c.post("/api/setup", json={"full_name": "Jules Lafargue", "username": "Jules", "email": "j@x.org",
                                   "password": "longenough", "load_demo": True})
    assert r.status_code == 201, r.text
    assert r.json()["user"]["role"] == "admin" and r.json()["user"]["username"] == "jules" and r.json()["demo_loaded"]
    assert c.get("/api/setup").json() == {"needs_setup": False}
    assert c.post("/api/setup", json={"full_name": "X", "username": "x2", "password": "longenough"}).status_code == 409
    assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "alice", "password": "benchtop123"}).status_code == 200
    import base64, json, time
    tok = c.post("/api/auth/login", json={"username": "jules", "password": "longenough", "remember": True}).json()["token"]
    exp = json.loads(base64.urlsafe_b64decode(tok.split(".")[0] + "=="))["exp"]
    assert exp - time.time() > 29 * 24 * 3600
print("ok")
'''
    env = {**os.environ, "BENCHTOP_DATA_DIR": str(tmp_path), "BENCHTOP_ADMIN_PASSWORD": "admin", "BENCHTOP_BACKUP_INTERVAL_HOURS": "0"}
    out = subprocess.run([sys.executable, "-c", code], cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr[-2000:]


def test_structure_check_and_hrms(client, users):
    h = users["alice"]
    r = client.get("/api/chem/structure-check", headers=h, params={"smiles": "COc1ccc(-c2ccccc2)cc1"}).json()
    assert r["unique_carbons"] == 9 and r["total_h"] == 12 and len(r["h_environments"]) == 6
    r = client.get("/api/chem/structure-check", headers=h, params={"smiles": "CC(=O)Nc1ccc(O)cc1"}).json()
    assert r["exchangeable_h"] == 2
    ions = {i["adduct"]: i for i in client.get("/api/chem/hrms", headers=h, params={"smiles": "COc1ccc(-c2ccccc2)cc1"}).json()["ions"]}
    assert ions["[M + H]+"]["formula"] == "C13H13O" and ions["[M + H]+"]["mz"] == pytest.approx(185.0961, abs=1e-4)
    assert ions["[M + Na]+"]["mz"] == pytest.approx(207.0780, abs=1e-4)
    assert client.get("/api/chem/hrms", headers=h, params={"smiles": "not a smiles"}).status_code == 422


def test_spectra_listing_for_overlay(client, users, notebook):
    h = users["alice"]
    e1 = new_exp(client, h, notebook, title="Overlay A")
    e2 = new_exp(client, h, notebook, title="Overlay B")
    up = lambda e, name: client.post(f"/api/experiments/{e['id']}/attachments", headers=h,
                                     files={"file": (name, io.BytesIO(b"##TITLE=x\n##END=\n"), "text/plain")}).json()
    a1, a2, a3 = up(e1, "crude_1H.jdx"), up(e1, "pure_1H.jdx"), up(e2, "sm_1H.jdx")
    up(e2, "photo.png")
    r = client.get("/api/spectra", headers=h, params={"near": a1["id"]}).json()
    ids = [x["id"] for x in r]
    assert a1["id"] not in ids and ids[0] == a2["id"] and a3["id"] in ids
    assert not any(x["filename"] == "photo.png" for x in r)
    assert [x["id"] for x in client.get("/api/spectra", headers=h, params={"q": "overlay b"}).json()] == [a3["id"]]


def test_dashboard_and_prefs(client, users, admin, notebook):
    a, b = users["alice"], users["bob"]
    exp = new_exp(client, a, notebook, title="Dash returned")
    client.post(f"/api/experiments/{exp['id']}/sign", headers=a, json={"password": "password123"})
    client.post(f"/api/experiments/{exp['id']}/reject", headers=b, json={"password": "password123", "comment": "add yield"})
    m = client.post("/api/materials", headers=a, json={"name": "Dash reagent", "smiles": "CC(C)(C)O"}).json()
    lot = client.post(f"/api/materials/{m['id']}/lots", headers=a, json={"amount": 10, "unit": "g", "expires_at": "2026-10-20T00:00:00"}).json()
    client.post(f"/api/lots/{lot['id']}/transactions", headers=a, json={"amount": 9, "unit": "g"})
    me = client.patch("/api/auth/me", headers=a, json={"preferences": {"pinned": [exp["id"], "x"], "dashboard": {"widgets": [
        {"i": "a", "type": "list", "list": "returned", "x": 0, "y": 0, "w": 6, "h": 4},
        {"i": "b", "type": "note", "text": "buy more Pd", "x": 99, "y": 0, "w": 1, "h": 4},
        {"i": "c", "type": "evil", "x": 0, "y": 0}]}}}).json()
    ws = me["preferences"]["dashboard"]["widgets"]
    assert me["preferences"]["pinned"] == [exp["id"]] and [w["type"] for w in ws] == ["list", "note"]
    assert ws[1]["x"] == 11 and ws[1]["w"] == 2  # clamped
    d = client.get("/api/dashboard", headers=a).json()
    assert any(r["id"] == exp["id"] and r["reason"] == "add yield" for r in d["lists"]["returned"])
    assert [p["id"] for p in d["lists"]["pinned"]] == [exp["id"]]
    assert any(r["lot_id"] == lot["id"] for r in d["inventory"]["low"])
    assert any(r["lot_id"] == lot["id"] for r in d["inventory"]["expiring"])
    assert d["stats"]["this_month"] >= 1
