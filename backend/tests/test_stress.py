"""Stress scenarios: realistic usage plus hostile and malformed input across every feature.
Each test states what *should* happen; a failure is a bug (or a gap worth deciding on)."""
import io
import json
import threading
import zipfile

import httpx
import pytest

from app import pubchem
from tests.conftest import _login
import tests.test_v7 as V7

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def mock_pubchem(monkeypatch):
    monkeypatch.setattr(pubchem, "MIN_INTERVAL", 0.0)
    pubchem.set_transport(httpx.MockTransport(V7.handler))
    V7.STATE.update(ethanol=V7.ETHANOL, offline=False)
    yield
    pubchem.set_transport(None)


@pytest.fixture(scope="module")
def S(client, users, admin):
    """Shared scenario state: a notebook and a fully populated experiment owned by alice."""
    a = users["alice"]
    nb = client.post("/api/notebooks", headers=a, json={"name": "Stress NB"}).json()
    return {"a": a, "b": users["bob"], "v": users["vic"], "admin": admin, "nb": nb}


def new_exp(client, h, nb, title="Stress experiment"):
    r = client.post("/api/experiments", headers=h, json={"title": title, "notebook_id": nb["id"]})
    assert r.status_code == 201, r.text
    return client.get(f"/api/experiments/{r.json()['id']}", headers=h).json()


def entry(exp, kind):
    return next(e for e in exp["entries"] if e["kind"] == kind)


def put(client, h, e, content, **kw):
    return client.put(f"/api/entries/{e['id']}", headers=h, json={"content": content, **kw})


# ============================================================== accounts & access

def test_login_wrong_password_and_case_insensitive(client, S):
    assert client.post("/api/auth/login", json={"username": "alice", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "ALICE", "password": "password123"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "", "password": ""}).status_code in (401, 422)
    assert client.post("/api/auth/login", json={"username": "x" * 10000, "password": "y"}).status_code in (401, 422)


def test_garbage_and_expired_tokens(client):
    for tok in ("garbage", "a.b.c", "", "Bearer"):
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    assert client.get("/api/experiments").status_code == 401


def test_viewer_cannot_write_anything(client, S):
    v, a = S["v"], S["a"]
    exp = new_exp(client, a, S["nb"])
    assert client.post("/api/notebooks", headers=v, json={"name": "x"}).status_code == 403
    assert client.post("/api/experiments", headers=v, json={"title": "x"}).status_code == 403
    assert put(client, v, entry(exp, "text"), {"html": "<p>hi</p>"}).status_code == 403
    assert client.post("/api/materials", headers=v, json={"name": "x"}).status_code == 403
    assert client.post(f"/api/experiments/{exp['id']}/clone", headers=v, params={"scale": 2}).status_code == 403
    assert client.get(f"/api/experiments/{exp['id']}", headers=v).status_code == 200  # but can read


def test_other_scientist_cannot_edit_my_experiment(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    r = put(client, S["b"], entry(exp, "text"), {"html": "<p>bob was here</p>"})
    assert r.status_code == 403, f"bob edited alice's experiment: {r.status_code}"


def test_deactivated_user_token_stops_working(client, S):
    admin = S["admin"]
    client.post("/api/users", headers=admin, json={"username": "temp1", "password": "password123", "role": "scientist"})
    h = _login(client, "temp1", "password123")
    uid = client.get("/api/auth/me", headers=h).json()["id"]
    assert client.patch(f"/api/users/{uid}", headers=admin, json={"active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.post("/api/auth/login", json={"username": "temp1", "password": "password123"}).status_code in (401, 403)


def test_username_and_password_changes(client, S):
    admin = S["admin"]
    client.post("/api/users", headers=admin, json={"username": "temp2", "password": "password123", "role": "scientist"})
    h = _login(client, "temp2", "password123")
    assert client.post("/api/auth/username", headers=h, json={"new_username": "alice", "password": "password123"}).status_code in (400, 409)
    assert client.post("/api/auth/username", headers=h, json={"new_username": "bad name!", "password": "password123"}).status_code in (400, 422)
    assert client.post("/api/auth/password", headers=h, json={"current_password": "password123", "new_password": "x"}).status_code in (400, 422)
    assert client.post("/api/auth/password", headers=h, json={"current_password": "wrong", "new_password": "longenough1"}).status_code in (400, 401, 403)
    assert client.post("/api/auth/password", headers=h, json={"current_password": "password123", "new_password": "longenough1"}).status_code == 200
    _login(client, "temp2", "longenough1")
    assert client.patch("/api/auth/me", headers=h, json={"email": "not-an-email"}).status_code in (400, 422)
    assert client.patch("/api/auth/me", headers=h, json={"full_name": "Ünïcødé Ñame 化学"}).status_code == 200


def test_setup_cannot_be_rerun(client):
    assert client.get("/api/setup").json()["needs_setup"] is False
    r = client.post("/api/setup", json={"full_name": "Evil", "username": "evil", "password": "password123"})
    assert r.status_code in (403, 409), f"setup re-run allowed: {r.status_code}"


# ============================================================== experiments & records

def test_unicode_and_hostile_titles(client, S):
    for title in ("Δ-élimination ✓ 化学", "<script>alert(1)</script>", "a" * 500, "  padded  ", "Robert'); DROP TABLE experiment;--"):
        r = client.post("/api/experiments", headers=S["a"], json={"title": title, "notebook_id": S["nb"]["id"]})
        assert r.status_code in (201, 422), (title[:20], r.status_code)
        if r.status_code == 201:
            html = client.get(f"/api/experiments/{r.json()['id']}/export", headers=S["a"]).text
            assert "<script>alert(1)</script>" not in html, "unescaped title in HTML export"
    assert client.post("/api/experiments", headers=S["a"], json={"title": ""}).status_code == 422
    assert client.post("/api/experiments", headers=S["a"], json={"title": "   "}).status_code in (400, 422)


def test_text_section_is_sanitised(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    r = put(client, S["a"], entry(exp, "text"), {"html": '<p onclick="x()">hi<img src=x onerror=alert(1)><script>bad()</script><a href="javascript:x">l</a></p>'})
    html = r.json()["content"]["html"]
    assert "onerror" not in html and "<script" not in html and "javascript:" not in html and "onclick" not in html


def test_version_conflict(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    e = entry(exp, "text")
    assert put(client, S["a"], e, {"html": "<p>one</p>"}, expected_version=e["version"]).status_code == 200
    assert put(client, S["a"], e, {"html": "<p>stale</p>"}, expected_version=e["version"]).status_code == 409


def test_reorder_with_bad_ids(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    ids = [e["id"] for e in exp["entries"]]
    assert client.post(f"/api/experiments/{exp['id']}/reorder", headers=S["a"], json={"entry_ids": list(reversed(ids))}).status_code == 200
    r = client.post(f"/api/experiments/{exp['id']}/reorder", headers=S["a"], json={"entry_ids": [999999]})
    assert r.status_code in (400, 404, 422), f"reorder with foreign ids: {r.status_code}"
    r = client.post(f"/api/experiments/{exp['id']}/reorder", headers=S["a"], json={"entry_ids": ids[:2]})
    assert r.status_code in (200, 400), r.status_code
    after = client.get(f"/api/experiments/{exp['id']}", headers=S["a"]).json()
    assert len(after["entries"]) == len(ids), "partial reorder lost sections"


def test_full_signing_lifecycle(client, S):
    a, b = S["a"], S["b"]
    exp = new_exp(client, a, S["nb"], "Lifecycle")
    eid = exp["id"]
    assert client.post(f"/api/experiments/{eid}/sign", headers=a, json={"password": "wrong"}).status_code in (401, 403)
    assert client.post(f"/api/experiments/{eid}/witness", headers=b, json={"password": "password123"}).status_code in (400, 409), "witnessed an unsigned record"
    assert client.post(f"/api/experiments/{eid}/sign", headers=b, json={"password": "password123"}).status_code in (403,), "non-owner signed as author"
    assert client.post(f"/api/experiments/{eid}/sign", headers=a, json={"password": "password123"}).status_code == 200
    assert client.post(f"/api/experiments/{eid}/sign", headers=a, json={"password": "password123"}).status_code in (400, 409), "signed twice"
    assert put(client, a, entry(exp, "text"), {"html": "<p>after signing</p>"}).status_code in (403, 409), "edited a signed record"
    assert client.post(f"/api/experiments/{eid}/entries", headers=a, json={"kind": "text"}).status_code in (403, 409)
    assert client.post(f"/api/experiments/{eid}/witness", headers=a, json={"password": "password123"}).status_code in (400, 403), "author witnessed own record"
    assert client.post(f"/api/experiments/{eid}/witness", headers=S["v"], json={"password": "password123"}).status_code == 403, "viewer witnessed"
    # reject, fix, resubmit, witness
    assert client.post(f"/api/experiments/{eid}/reject", headers=b, json={"password": "password123", "comment": "Add the TLC"}).status_code == 200
    dash = client.get("/api/dashboard", headers=a).json()
    assert any(x["id"] == eid and x["reason"] == "Add the TLC" for x in dash["lists"]["returned"])
    assert put(client, a, entry(exp, "text"), {"html": "<p>TLC added</p>"}).status_code == 200
    assert client.post(f"/api/experiments/{eid}/sign", headers=a, json={"password": "password123"}).status_code == 200
    assert client.post(f"/api/experiments/{eid}/witness", headers=b, json={"password": "password123"}).status_code == 200
    v = client.get(f"/api/experiments/{eid}/verify", headers=a).json()
    assert v["intact"]
    assert client.post(f"/api/experiments/{eid}/withdraw", headers=a, json={"password": "password123"}).status_code in (400, 409), "withdrew a witnessed record"
    assert client.post(f"/api/experiments/{eid}/void", headers=b, json={"password": "password123", "comment": "x"}).status_code == 403, "non-owner voided"
    assert client.post(f"/api/experiments/{eid}/void", headers=a, json={"password": "password123", "comment": "duplicate"}).status_code == 200
    assert client.post(f"/api/experiments/{eid}/void", headers=a, json={"password": "password123", "comment": "again"}).status_code in (400, 409)
    # deleting a section of a voided record must fail
    sec = client.get(f"/api/experiments/{eid}", headers=a).json()["entries"][0]
    assert client.delete(f"/api/entries/{sec['id']}", headers=a).status_code in (403, 409)


def test_audit_chain_survives_load(client, S):
    for i in range(30):
        client.post("/api/notebooks", headers=S["a"], json={"name": f"bulk {i}"})
    r = client.get("/api/audit/verify", headers=S["admin"]).json()
    assert r.get("intact", r.get("ok")), r


# ============================================================== stoichiometry edge cases

def stoich(client, h, rows, target=None):
    r = client.post("/api/chem/stoichiometry", headers=h, json={"rows": rows, "target_conc_m": target})
    assert r.status_code == 200, r.text
    return r.json()


def test_stoichiometry_garbage_rows(client, S):
    h = S["a"]
    cases = [
        [],
        [{"role": "reactant"}],
        [{"role": "bogus", "basis": "bogus", "mass_mg": "abc"}],
        [{"role": "reactant", "smiles": "not a smiles", "mass_mg": 100, "limiting": True}],
        [{"role": "reactant", "smiles": "CCO", "mass_mg": -50, "limiting": True}],
        [{"role": "reactant", "smiles": "CCO", "mass_mg": 0, "limiting": True}, {"role": "reagent", "smiles": "CC", "equiv": 2}],
        [{"role": "reactant", "smiles": "CCO", "mass_mg": 1e30, "limiting": True}],
        [{"role": "reactant", "smiles": "CCO", "mass_mg": 46, "limiting": True, "purity": 0}],
        [{"role": "reactant", "smiles": "CCO", "mass_mg": 46, "limiting": True, "purity": -5}],
        [{"role": "solvent", "basis": "fill"}],
        [{"role": "product", "smiles": "CC", "actual_mass_mg": 30}],
        [{"role": "reactant", "smiles": "CCO", "basis": "volume", "volume_ml": 1, "limiting": True}],  # no density
        [{"role": "reactant", "smiles": "CCO", "mass_mg": "1,5", "limiting": True}],
        [{"role": "reactant", "mw": "NaN", "mass_mg": 10, "limiting": True}],
        [{"role": "reactant", "mw": "Infinity", "mass_mg": 10, "limiting": True}],
    ]
    for rows in cases:
        out = stoich(client, h, rows)
        json.dumps(out, allow_nan=False)  # must be valid JSON (no NaN/Infinity)


def test_stoichiometry_two_limiting_and_yield_over_100(client, S):
    out = stoich(client, S["a"], [
        {"role": "reactant", "smiles": "CCO", "mass_mg": 46.07, "limiting": True},
        {"role": "reactant", "smiles": "CC(=O)O", "mass_mg": 60.05, "limiting": True},
        {"role": "product", "smiles": "CCOC(C)=O", "actual_mass_mg": 200}])
    assert sum(1 for r in out["rows"] if r["limiting"]) == 1 and out["warnings"]
    prod = next(r for r in out["rows"] if r["role"] == "product")
    assert prod["yield_pct"] > 100
    assert any("100" in w or "yield" in w.lower() for w in out["warnings"]), "no warning for yield > 100%"


def test_green_metrics_robust(client, S):
    for rows in ([{"role": "product", "smiles": "CC", "actual_mass_mg": 10}],
                 [{"role": "reactant", "smiles": "CCO", "mass_mg": 46, "limiting": True}, {"role": "product", "smiles": "CC", "actual_mass_mg": 0}],
                 [{"role": "reactant", "smiles": "CCO", "mass_mg": 46, "limiting": True}, {"role": "solvent", "smiles": "O", "volume_ml": 5},
                  {"role": "product", "smiles": "CCOCC", "actual_mass_mg": 30}]):
        m = stoich(client, S["a"], rows)["totals"]["metrics"]
        json.dumps(m, allow_nan=False)
    m = stoich(client, S["a"], [{"role": "reactant", "smiles": "CCO", "mass_mg": 46, "limiting": True},
                                {"role": "solvent", "smiles": "O", "volume_ml": 5}, {"role": "product", "smiles": "CCOCC", "actual_mass_mg": 30}])["totals"]["metrics"]
    assert "pmi" not in m and m["missing_mass"], "PMI shown although a solvent mass is unknown"


def test_reaction_parse_garbage(client, S):
    for rxn in ("", ">>", "CCO>>", "garbage", "C1CC>>CC", "CCO.CC>>" + "C" * 400, "[Na+].[Cl-]>>[Na+].[Cl-]"):
        r = client.post("/api/chem/reaction/parse", headers=S["a"], json={"reaction": rxn})
        assert r.status_code in (200, 422), (rxn[:20], r.status_code)


def test_properties_and_depict_garbage(client, S):
    for s in ("", "C" * 2000, "[Xx]", "c1ccccc", "\u0000", "C(C)(C)(C)(C)C"):
        r = client.post("/api/chem/properties", headers=S["a"], json={"structure": s})
        assert r.status_code in (200, 422), (s[:10], r.status_code)
        r = client.get("/api/chem/depict", params={"smiles": s})
        assert r.status_code in (200, 422), ("depict", s[:10], r.status_code)
    r = client.get("/api/chem/depict", params={"smiles": "CCO", "w": 100000, "h": 100000})
    assert r.status_code in (200, 400, 422) and len(r.content) < 5_000_000, "huge depiction allowed"


def test_structure_check_and_hrms_garbage(client, S):
    for s in ("", "xyz", "[Na+].[Cl-]", "[Fe]"):
        assert client.get("/api/chem/structure-check", headers=S["a"], params={"smiles": s}).status_code in (200, 422)
        assert client.get("/api/chem/hrms", headers=S["a"], params={"smiles": s}).status_code in (200, 422)


# ============================================================== scale-up

def test_scale_up_extremes(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    rxn = entry(exp, "reaction")
    rows = [{"role": "reactant", "name": "EtOH", "smiles": "CCO", "basis": "mass", "mass_mg": 46.07, "limiting": True},
            {"role": "reagent", "name": "soln", "smiles": "[Li+].[OH-]", "basis": "volume", "volume_ml": 1.0, "conc_m": 1.0},
            {"role": "solvent", "name": "THF", "smiles": "C1CCOC1", "basis": "fill", "density": 0.889},
            {"role": "product", "smiles": "CCOCC", "actual_mass_mg": 50}]
    put(client, S["a"], rxn, {"rows": rows, "conditions": {"target_conc_m": 0.5}})
    for f in (0.001, 1000, 2.5):
        r = client.post(f"/api/experiments/{exp['id']}/clone", headers=S["a"], params={"scale": f})
        assert r.status_code == 201, (f, r.text)
        c = entry(client.get(f"/api/experiments/{r.json()['id']}", headers=S["a"]).json(), "reaction")["content"]
        lim = next(x for x in c["rows"] if x["limiting"])
        assert lim["mass_mg"] == pytest.approx(46.07 * f, rel=1e-3)
        sol = next(x for x in c["rows"] if x["role"] == "solvent")
        assert sol["volume_ml"] == pytest.approx((1.0 * f / 0.5) - 1.0 * f, rel=1e-2), "fill solvent not recalculated"
    for bad in (-1, 0, 1e9, float("nan")):
        r = client.post(f"/api/experiments/{exp['id']}/clone", headers=S["a"], params={"scale": bad})
        assert r.status_code in (400, 422), (bad, r.status_code)


# ============================================================== inventory

def test_inventory_edge_cases(client, S):
    a = S["a"]
    m = client.post("/api/materials", headers=a, json={"name": "Stress butanone", "smiles": "CCC(C)=O", "kind": "solvent", "density": 0.784}).json()
    assert client.post("/api/materials", headers=a, json={"name": "Dup butanone", "smiles": "CC(=O)CC"}).status_code == 409
    assert client.post("/api/materials", headers=a, json={"name": "Bad", "smiles": "zzz"}).status_code == 422
    assert client.post("/api/materials", headers=a, json={"name": "No structure mix", "kind": "other"}).status_code == 201
    lot = client.post(f"/api/materials/{m['id']}/lots", headers=a, json={"amount": 100, "unit": "mL"}).json()
    assert client.post(f"/api/materials/{m['id']}/lots", headers=a, json={"amount": -5, "unit": "mL"}).status_code in (400, 422), "negative lot"
    assert client.post(f"/api/materials/{m['id']}/lots", headers=a, json={"amount": 5, "unit": "parsecs"}).status_code in (400, 422)
    # g -> mL via density
    r = client.post(f"/api/lots/{lot['id']}/transactions", headers=a, json={"amount": 7.84, "unit": "g"})
    assert r.status_code == 201 and r.json()["lot"]["amount"] == pytest.approx(90.0, rel=1e-3)
    # overdraw
    r = client.post(f"/api/lots/{lot['id']}/transactions", headers=a, json={"amount": 1000, "unit": "mL"})
    assert r.status_code in (400, 409), f"overdrew a lot: {r.status_code} {r.text[:120]}"
    assert client.post(f"/api/lots/{lot['id']}/transactions", headers=a, json={"amount": -3, "unit": "mL"}).status_code in (400, 422), "negative withdrawal"
    assert client.post(f"/api/lots/{lot['id']}/transactions", headers=a, json={"amount": 1, "unit": "mL", "direction": "sideways"}).status_code in (400, 422)
    # reversing twice
    tx = client.post(f"/api/lots/{lot['id']}/transactions", headers=a, json={"amount": 10, "unit": "mL"}).json()["transaction_id"]
    assert client.post(f"/api/lots/transactions/{tx}/reverse", headers=a).status_code == 201
    assert client.post(f"/api/lots/transactions/{tx}/reverse", headers=a).status_code in (400, 409), "reversed twice"
    # mass withdrawal from a material with no density but a mL lot
    m2 = client.post("/api/materials", headers=a, json={"name": "No density liquid", "smiles": "CCCCCCCC"}).json()
    lot2 = client.post(f"/api/materials/{m2['id']}/lots", headers=a, json={"amount": 10, "unit": "mL"}).json()
    assert client.post(f"/api/lots/{lot2['id']}/transactions", headers=a, json={"amount": 1, "unit": "g"}).status_code in (400, 422)


# ============================================================== safety

def test_safety_unknown_and_failing_service(client, S, monkeypatch):
    a = S["a"]
    assert client.get("/api/lookup", headers=a, params={"q": "unobtainium"}).status_code == 404
    assert client.get("/api/lookup", headers=a, params={"q": "   "}).status_code == 400
    assert client.get("/api/lookup", headers=a, params={"q": "C" * 5000}).status_code in (404, 400, 422)

    def busy(request):
        return httpx.Response(503)
    pubchem.set_transport(httpx.MockTransport(busy))
    r = client.get("/api/lookup", headers=a, params={"q": "ethanol"})
    assert r.status_code == 502 and "busy" in r.json()["detail"]

    def junk(request):
        return httpx.Response(200, content=b"<html>not json</html>")
    pubchem.set_transport(httpx.MockTransport(junk))
    assert client.get("/api/lookup", headers=a, params={"q": "ethanol"}).status_code == 502

    def weird(request):
        return httpx.Response(200, json={"Record": {"Section": "not a list", "Reference": None}, "IdentifierList": {"CID": [702]}})
    pubchem.set_transport(httpx.MockTransport(weird))
    r = client.get("/api/lookup", headers=a, params={"q": "ethanol"})
    assert r.status_code in (200, 502), f"malformed PubChem JSON crashed lookup: {r.status_code}"


def test_experiment_safety_many_chemicals_and_offline(client, S):
    a = S["a"]
    exp = new_exp(client, a, S["nb"], "Many chemicals")
    rows = [{"role": "reagent", "name": f"alkane {n}", "smiles": "C" * n, "basis": "mass", "mass_mg": 10} for n in range(1, 25)]
    rows[0]["role"], rows[0]["limiting"] = "reactant", True
    put(client, a, entry(exp, "reaction"), {"rows": rows, "conditions": {}})
    r = client.get(f"/api/experiments/{exp['id']}/safety", headers=a)
    assert r.status_code == 200 and len(r.json()["chemicals"]) == 24
    V7.STATE["offline"] = True
    r = client.get(f"/api/experiments/{exp['id']}/safety", headers=a, params={"refresh": True})
    assert r.status_code == 200, "offline refresh crashed the risk assessment"


def test_safety_for_name_only_material(client, S):
    a = S["a"]
    m = client.post("/api/materials", headers=a, json={"name": "Ethanol", "kind": "solvent", "cas": "64-17-5"})
    if m.status_code == 201:
        r = client.post(f"/api/materials/{m.json()['id']}/safety/refresh", headers=a)
        assert r.status_code == 200 and r.json()["status"] in ("ok", "unclassified")
    assert client.post("/api/materials/999999/safety/refresh", headers=a).status_code == 404
    nm = client.post("/api/materials", headers=a, json={"name": "Mystery goo", "kind": "other"}).json()
    r = client.post(f"/api/materials/{nm['id']}/safety/refresh", headers=a)
    assert r.status_code == 200 and r.json()["status"] == "not_found"


def test_safety_snapshot_garbage(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    sec = entry(exp, "safety")
    for snap in ("string", [1, 2], {"chemicals": "nope"}, {"chemicals": [{"name": 5}]}, {"x": "y" * 300_000}):
        r = put(client, S["a"], sec, {"snapshot": snap})
        assert r.status_code == 200, (str(snap)[:30], r.status_code)
    html = client.get(f"/api/experiments/{exp['id']}/export", headers=S["a"])
    assert html.status_code == 200, "export crashed on a malformed safety snapshot"
    md = client.get(f"/api/experiments/{exp['id']}/export", headers=S["a"], params={"format": "md"})
    assert md.status_code == 200, "markdown export crashed on a malformed safety snapshot"


# ============================================================== screening

def test_screen_garbage(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    e = client.post(f"/api/experiments/{exp['id']}/entries", headers=S["a"], json={"kind": "screen"}).json()
    for body in ({"x_levels": "notalist"}, {"x_levels": [None, 5, {"a": 1}] * 10, "y_levels": []},
                 {"cells": {"0,0": {"value": "NaN"}, "0,1": {"value": "1e400"}, "-1,0": {"value": 1}, "0,0,0": {}}},
                 {"cells": "nope"}, {"higher_is_better": "maybe"}):
        r = put(client, S["a"], e, body)
        assert r.status_code == 200, (str(body)[:40], r.status_code, r.text[:200])
        json.dumps(r.json(), allow_nan=False)
    assert client.get(f"/api/experiments/{exp['id']}/export", headers=S["a"], params={"format": "md"}).status_code == 200


# ============================================================== attachments & spectra

def test_attachment_edge_cases(client, S):
    a = S["a"]
    exp = new_exp(client, a, S["nb"])
    up = lambda name, data: client.post(f"/api/experiments/{exp['id']}/attachments", headers=a, files={"file": (name, data)})  # noqa: E731
    assert up("empty.jdx", b"").status_code in (201, 400)
    r = up("../../etc/passwd", b"root:x")
    assert r.status_code in (201, 400)
    if r.status_code == 201:
        assert "/" not in r.json()["filename"] and ".." not in r.json()["filename"], "path in stored filename"
    assert up("ünïcødé spectrum 化学.jdx", b"##TITLE=x\n##END=").status_code == 201
    big = b"0" * (51 * 1024 * 1024)
    assert up("huge.csv", big).status_code == 413, "over-limit upload accepted"
    r = up("ok.png", PNG)
    att = r.json()
    d = client.get(f"/api/attachments/{att['id']}", headers=a)
    assert d.status_code == 200 and d.content == PNG
    assert client.get("/api/attachments/999999", headers=a).status_code == 404
    # someone without access? (all users can read in this ELN) - at least require auth
    assert client.get(f"/api/attachments/{att['id']}").status_code == 401


def test_saved_analysis_garbage(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    ch = entry(exp, "characterization")
    bad = [{"integrals": "x"}, {"integrals": [[1]], "ref_idx": -5, "ref_value": "a"}, {"integrals": [[float("1e308"), 1]] * 1000},
           {"labels": [{"mult": "<script>", "j": "x" * 10000}]}, {"impurities": [{"short": 1, "signals": "a"}]}, {"overlays": ["x", -1, 99999999]}]
    for an in bad:
        r = put(client, S["a"], ch, {"items": [{"technique": "1H NMR", "data": "", "analysis": an}]})
        assert r.status_code == 200, (str(an)[:40], r.status_code, r.text[:200])
        json.dumps(r.json(), allow_nan=False)


def test_spectra_listing_and_search(client, S):
    a = S["a"]
    assert client.get("/api/spectra", headers=a, params={"q": "'; drop", "limit": 100000}).status_code == 200
    for mode in ("text", "substructure", "similarity"):
        for q in ("", "%", "_", "c1ccccc1", "[invalid", "化学", "x" * 3000):
            r = client.get("/api/search", headers=a, params={"q": q, "mode": mode})
            assert r.status_code in (200, 400, 422), (mode, q[:10], r.status_code)


# ============================================================== exports & reports

def test_exports_on_every_kind_of_record(client, S):
    a = S["a"]
    exp = new_exp(client, a, S["nb"], 'Quotes "and" commas, | pipes\nnewline')
    for kind in ("table", "file", "screen", "safety"):
        client.post(f"/api/experiments/{exp['id']}/entries", headers=a, json={"kind": kind})
    put(client, a, entry(exp, "reaction"), {"rows": [{"role": "reactant", "name": 'A, "quoted" | name', "smiles": "CCO", "basis": "mass", "mass_mg": 46, "limiting": True},
                                                     {"role": "product", "name": "B\nnewline", "smiles": "CC", "actual_mass_mg": 10}], "conditions": {}})
    for fmt in ("html", "md", "json"):
        r = client.get(f"/api/experiments/{exp['id']}/export", headers=a, params={"format": fmt})
        assert r.status_code == 200, (fmt, r.status_code)
    assert client.get(f"/api/experiments/{exp['id']}/export", headers=a, params={"format": "exe"}).status_code in (200, 400, 422)
    w = client.get(f"/api/experiments/{exp['id']}/writeup", headers=a)
    assert w.status_code == 200
    csv = client.get("/api/reactions", headers=a, params={"format": "csv"})
    assert csv.status_code == 200
    import csv as csvmod
    rows = list(csvmod.reader(io.StringIO(csv.text)))
    assert all(len(r) == len(rows[0]) for r in rows), "CSV rows have inconsistent column counts"


def test_obsidian_export_and_backup(client, S, tmp_path):
    from sqlmodel import Session

    from app.db import engine
    from app.export_md import sync_vault
    with Session(engine) as s:
        res = sync_vault(s, tmp_path / "vault")
    assert res["written"] > 0
    names = [p.name for p in (tmp_path / "vault").rglob("*.md")]
    assert all("/" not in n and "\n" not in n for n in names)
    r = client.post("/api/admin/backups", headers=S["admin"])
    assert r.status_code == 201
    lst = client.get("/api/admin/backups", headers=S["admin"]).json()
    assert lst and zipfile.is_zipfile(lst[0]["path"]) if isinstance(lst, list) and lst and "path" in lst[0] else True
    assert client.post("/api/admin/backups", headers=S["a"]).status_code == 403


# ============================================================== dashboard & preferences

def test_dashboard_preferences_garbage(client, S):
    a = S["a"]
    for prefs in ({"dashboard": {"widgets": "x"}}, {"dashboard": {"widgets": [{"i": "a", "type": "nope"}] * 100}},
                  {"dashboard": {"widgets": [{"i": "a", "type": "list", "list": "secret", "x": -100, "w": 9999, "text": "x" * 100000}]}},
                  {"pinned": ["a", None, 99999999, -1]}, {"theme": "neon"}, {"unknown_key": 1}, {"pinned": list(range(10000))}):
        r = client.patch("/api/auth/me", headers=a, json={"preferences": prefs})
        assert r.status_code in (200, 422), (str(prefs)[:40], r.status_code)
    d = client.get("/api/dashboard", headers=a)
    assert d.status_code == 200, "dashboard crashed after odd preferences"
    me = client.get("/api/auth/me", headers=a).json()
    assert len(json.dumps(me.get("preferences") or {})) < 50_000


def test_pinned_deleted_or_foreign_experiment(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    client.patch("/api/auth/me", headers=S["b"], json={"preferences": {"pinned": [exp["id"], 123456]}})
    d = client.get("/api/dashboard", headers=S["b"]).json()
    assert [p["id"] for p in d["lists"]["pinned"]] == [exp["id"]]


# ============================================================== concurrency

def test_parallel_writes_do_not_lock_the_database(client, S):
    exp = new_exp(client, S["a"], S["nb"])
    errors = []

    def worker(n):
        try:
            for k in range(5):
                r = client.post(f"/api/experiments/{exp['id']}/entries", headers=S["a"], json={"kind": "text", "title": f"t{n}-{k}"})
                if r.status_code != 201:
                    errors.append(r.status_code)
                r = client.post("/api/materials", headers=S["a"], json={"name": f"par {n}-{k}", "kind": "other"})
                if r.status_code != 201:
                    errors.append(r.status_code)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors[:5]
    after = client.get(f"/api/experiments/{exp['id']}", headers=S["a"]).json()
    positions = [e["position"] for e in after["entries"]]
    assert len(positions) == len(set(positions)), "duplicate section positions after concurrent adds"
    assert client.get("/api/audit/verify", headers=S["admin"]).json().get("intact", True), "audit chain broken by concurrent writes"
