"""v0.7: PubChem lookup, GHS safety store with refresh and change detection, risk rules,
green metrics, scale-up, med-chem descriptors, screening sections."""
import json
from datetime import timedelta

import httpx
import pytest

from app import pubchem, safety
from app.models import ChemSafety, utcnow
from tests.conftest import _login  # noqa: F401  (fixture helpers)


def _ghs_record(cid, title, groups, refs):
    """A PUG View 'GHS Classification' record shaped like PubChem's real output."""
    info = []
    for ref, g in groups.items():
        if g.get("pictograms"):
            info.append({"ReferenceNumber": ref, "Name": "Pictogram(s)", "Value": {"StringWithMarkup": [{"String": "          ", "Markup": [
                {"Start": 0, "Length": 1, "URL": f"https://pubchem.ncbi.nlm.nih.gov/images/ghs/{p}.svg", "Type": "Icon", "Extra": n} for p, n in g["pictograms"]]}]}})
        if g.get("signal"):
            info.append({"ReferenceNumber": ref, "Name": "Signal", "Value": {"StringWithMarkup": [{"String": g["signal"], "Markup": [{"Type": "Color", "Extra": "GHSDanger"}]}]}})
        if g.get("hazards"):
            info.append({"ReferenceNumber": ref, "Name": "GHS Hazard Statements", "Value": {"StringWithMarkup": [{"String": h} for h in g["hazards"]]}})
        if g.get("precautions"):
            info.append({"ReferenceNumber": ref, "Name": "Precautionary Statement Codes", "Value": {"StringWithMarkup": [{"String": g["precautions"]}]}})
        if g.get("summary"):
            info.append({"ReferenceNumber": ref, "Name": "ECHA C&L Notifications Summary", "Value": {"StringWithMarkup": [{"String": g["summary"]}]}})
    return {"Record": {"RecordType": "CID", "RecordNumber": cid, "RecordTitle": title,
                       "Section": [{"TOCHeading": "Safety and Hazards", "Section": [{"TOCHeading": "Hazards Identification", "Section": [
                           {"TOCHeading": "GHS Classification", "Information": info}]}]}],
                       "Reference": [{"ReferenceNumber": k, **v} for k, v in refs.items()]}}


ETHANOL = _ghs_record(702, "Ethanol", {
    157: {"pictograms": [("GHS02", "Flammable")], "signal": "Danger",
          "hazards": ["H225: Highly Flammable liquid and vapor [Danger Flammable liquids]"],
          "precautions": "P210, P233, P240, P241, P242, P243, P280, P303+P361+P353, P370+P378, P403+P235, and P501"},
    60: {"pictograms": [("GHS02", "Flammable"), ("GHS07", "Irritant")], "signal": "Danger",
         "hazards": ["H225 (> 99.9%): Highly Flammable liquid and vapor [Danger Flammable liquids]",
                     "H319 (37.7%): Causes serious eye irritation [Warning Serious eye damage/eye irritation]"],
         "precautions": "P210, P233, P240, P241, P242, P243, P264+P265, P280, P305+P351+P338",
         "summary": "Aggregated GHS information provided per 13886 reports by companies from 79 notifications to the ECHA C&L Inventory."},
}, {157: {"SourceName": "Regulation (EC) No 1272/2008 of the European Parliament and of the Council", "Name": "ethanol; ethyl alcohol",
          "URL": "https://eur-lex.europa.eu/eli/reg/2008/1272/oj"},
    60: {"SourceName": "European Chemicals Agency (ECHA)", "Name": "Ethanol (EC: 200-578-6)", "URL": "https://chem.echa.europa.eu/substance-search?searchText=200-578-6"}})

NAH = _ghs_record(24758, "Sodium hydride", {
    5: {"pictograms": [("GHS02", "Flammable")], "signal": "Danger",
        "hazards": ["H260 (100%): In contact with water releases flammable gases which may ignite spontaneously [Danger Substances and mixtures which in contact with water, emit flammable gases]"],
        "precautions": "P223, P231+P232, P280, P335+P334, P370+P378, P402+P404, and P501",
        "summary": "Aggregated GHS information provided per 412 reports by companies from 9 notifications to the ECHA C&L Inventory."}},
    {5: {"SourceName": "European Chemicals Agency (ECHA)", "Name": "sodium hydride", "URL": "https://chem.echa.europa.eu/x"}})

DMF = _ghs_record(6228, "N,N-Dimethylformamide", {
    7: {"pictograms": [("GHS02", "Flammable"), ("GHS07", "Irritant"), ("GHS08", "Health Hazard")], "signal": "Danger",
        "hazards": ["H226: Flammable liquid and vapour [Warning Flammable liquids]",
                    "H312+H332: Harmful in contact with skin or if inhaled [Warning Acute toxicity, dermal; acute toxicity, inhalation]",
                    "H319: Causes serious eye irritation [Warning Serious eye damage/eye irritation]",
                    "H360D: May damage the unborn child [Danger Reproductive toxicity]"],
        "precautions": "P201, P202, P210, P261, P280, P308+P313"}},
    {7: {"SourceName": "Regulation (EC) No 1272/2008 of the European Parliament and of the Council", "Name": "N,N-dimethylformamide", "URL": "https://eur-lex.europa.eu"}})

STATE = {"ethanol": ETHANOL, "calls": 0, "offline": False}
CIDS = {"LFQSCWFLJHTTHZ-UHFFFAOYSA-N": 702, "ethanol": 702, "64-17-5": 702, "BRGKCJYDSHCXHH-UHFFFAOYSA-N": 24758, "sodium hydride": 24758,
        "ZMXDDKWLCZADIW-UHFFFAOYSA-N": 6228, "dmf": 6228}
GHS = {702: lambda: STATE["ethanol"], 24758: lambda: NAH, 6228: lambda: DMF}
PROPS = {702: {"Title": "Ethanol", "IsomericSMILES": "CCO", "MolecularFormula": "C2H6O", "MolecularWeight": "46.07", "InChIKey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"}}


def handler(request: httpx.Request) -> httpx.Response:
    STATE["calls"] += 1
    if STATE["offline"]:
        raise httpx.ConnectError("offline", request=request)
    path = request.url.path
    if "/cids/JSON" in path:
        ident = request.url.params.get("smiles") or path.split("/")[-3]
        ident = httpx.URL("http://x/" + ident).path[1:].lower() if not request.url.params.get("smiles") else ident
        cid = CIDS.get(ident) or CIDS.get(ident.upper())
        return httpx.Response(200, json={"IdentifierList": {"CID": [cid]}}) if cid else httpx.Response(404, json={"Fault": {}})
    if "/property/" in path:
        cid = int(path.split("/cid/")[1].split("/")[0])
        return httpx.Response(200, json={"PropertyTable": {"Properties": [{"CID": cid, **PROPS.get(cid, {})}]}})
    if path.endswith("/synonyms/JSON"):
        return httpx.Response(200, json={"InformationList": {"Information": [{"CID": 702, "Synonym": ["ethanol", "64-17-5", "64-17-6"]}]}})
    if "/pug_view/" in path:
        cid = int(path.split("/compound/")[1].split("/")[0])
        heading = request.url.params.get("heading")
        if heading == "Density":
            return httpx.Response(200, json={"Record": {"Section": [{"TOCHeading": "Density", "Information": [
                {"Value": {"StringWithMarkup": [{"String": "Relative density (water = 1): 0.79"}]}},
                {"Value": {"StringWithMarkup": [{"String": "0.7893 g/cu cm at 20 °C"}]}}]}]}})
        return httpx.Response(200, json=GHS[cid]()) if cid in GHS else httpx.Response(404, json={})
    return httpx.Response(404, json={})


@pytest.fixture(autouse=True)
def mock_pubchem(monkeypatch):
    monkeypatch.setattr(pubchem, "MIN_INTERVAL", 0.0)
    pubchem.set_transport(httpx.MockTransport(handler))
    STATE.update(ethanol=ETHANOL, offline=False)
    yield
    pubchem.set_transport(None)


@pytest.fixture
def H(client):
    return _login(client, "admin", "adminpass")


def test_parse_ghs_picks_harmonised_and_keeps_extras():
    d = pubchem.parse_ghs(ETHANOL, 702)
    assert d["source"]["name"].startswith("EU CLP harmonised")
    assert [h["code"] for h in d["hazards"]] == ["H225"]
    assert d["signal"] == "Danger" and [p["code"] for p in d["pictograms"]] == ["GHS02"]
    assert "P210" in d["precautions"] and "P303+P361+P353" in d["precautions"]
    # ECHA's H319 (37.7% of notifiers) is kept as an extra and counts towards warnings
    assert d["other_hazards"][0]["code"] == "H319" and d["other_hazards"][0]["max_pct"] == 37.7
    assert "H319" in pubchem.effective_codes(d)
    assert [f["key"] for f in d["flags"]] == ["flammable"]


def test_flags_and_combined_codes():
    d = pubchem.parse_ghs(DMF, 6228)
    keys = {f["key"] for f in d["flags"]}
    assert {"flammable", "cmr"} <= keys
    assert "H312+H332" in [h["code"] for h in d["hazards"]]
    assert pubchem.valid_cas("64-17-5") and not pubchem.valid_cas("64-17-6")


def test_lookup_registers_safety(client, H):
    r = client.get("/api/lookup", params={"q": "ethanol"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cid"] == 702 and d["cas"] == "64-17-5" and d["density"] == 0.7893 and d["formula"] == "C2H6O"
    assert d["safety"]["status"] == "ok" and d["safety"]["data"]["signal"] == "Danger"
    assert d["safety"]["age_days"] == 0 and not d["safety"]["stale"]


def test_material_safety_refresh_and_change_detection(client, H):
    r = client.post("/api/materials", headers=H, json={"name": "Ethanol (abs.)", "smiles": "CCO", "kind": "solvent"})
    m = r.json() if r.status_code == 201 else next(x for x in client.get("/api/materials", headers=H).json() if x["inchikey"] == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N")
    client.post(f"/api/materials/{m['id']}/safety/refresh", headers=H)
    got = client.get(f"/api/materials/{m['id']}", headers=H).json()
    assert got["safety"]["status"] == "ok" and got["safety"]["data"]["hazards"][0]["code"] == "H225"
    # the classification changes upstream; an old record gets refreshed by housekeeping
    STATE["ethanol"] = _ghs_record(702, "Ethanol", {157: {"pictograms": [("GHS02", "Flammable"), ("GHS08", "Health Hazard")], "signal": "Danger",
        "hazards": ["H225: Highly Flammable liquid and vapor [Danger Flammable liquids]", "H350: May cause cancer [Danger Carcinogenicity]"]}},
        {157: {"SourceName": "Regulation (EC) No 1272/2008 of the European Parliament and of the Council", "Name": "ethanol", "URL": ""}})
    from sqlmodel import Session, select

    from app.db import engine
    with Session(engine, expire_on_commit=False) as s:
        cs = s.exec(select(ChemSafety).where(ChemSafety.key == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N")).one()
        cs.fetched_at = utcnow() - timedelta(days=45)
        s.add(cs)
        s.commit()
        res = safety.refresh_due(s)
    assert res["changed"] >= 1
    got = client.get(f"/api/materials/{m['id']}", headers=H).json()["safety"]
    assert got["change"]["added"] == ["H350"] and got["change_unacknowledged"]
    dash = client.get("/api/dashboard", headers=H).json()
    assert any(c["material_id"] == m["id"] for c in dash["inventory"]["safety_changes"])
    audit = client.get("/api/audit", headers=H, params={"limit": 50}).json()
    events = audit if isinstance(audit, list) else audit.get("events", [])
    assert any(e["action"] == "safety_changed" for e in events)
    ack = client.post(f"/api/materials/{m['id']}/safety/acknowledge", headers=H)
    assert ack.status_code == 200 and not ack.json()["change_unacknowledged"]
    assert not any(c["material_id"] == m["id"] for c in client.get("/api/dashboard", headers=H).json()["inventory"]["safety_changes"])


def test_offline_keeps_old_data(client, H):
    m = next(x for x in client.get("/api/materials", headers=H).json() if x["inchikey"] == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N")
    STATE["offline"] = True
    r = client.post(f"/api/materials/{m['id']}/safety/refresh", headers=H)
    assert r.status_code == 200
    s = r.json()
    assert s["status"] == "ok" and "reach PubChem" in s["error"] and s["data"]["hazards"]


def test_experiment_safety_rules(client, H):
    nb = client.post("/api/notebooks", headers=H, json={"name": "Safety NB"}).json()
    exp = client.post("/api/experiments", headers=H, json={"title": "NaH alkylation", "notebook_id": nb["id"]}).json()
    entries = client.get(f"/api/experiments/{exp['id']}", headers=H).json()["entries"]
    assert [e["kind"] for e in entries][:3] == ["text", "reaction", "safety"]
    rxn = next(e for e in entries if e["kind"] == "reaction")
    rows = [{"role": "reactant", "name": "ethanol", "smiles": "CCO", "basis": "mass", "mass_mg": 46.07, "limiting": True},
            {"role": "reagent", "name": "sodium hydride", "smiles": "[Na+].[H-]", "basis": "equiv", "equiv": 1.2},
            {"role": "solvent", "name": "DMF", "smiles": "CN(C)C=O", "basis": "volume", "volume_ml": 5, "density": 0.944}]
    assert client.put(f"/api/entries/{rxn['id']}", headers=H, json={"content": {"rows": rows, "conditions": {}}}).status_code == 200
    r = client.get(f"/api/experiments/{exp['id']}/safety", headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    texts = " ".join(w["text"] for w in d["warnings"])
    assert "Sodium hydride in DMF" in texts
    assert "Water-reactive reagent together with water or an alcohol" in texts
    assert "reproductive toxin" in texts
    assert d["warnings"][0]["level"] == "danger"
    assert any("inert gas" in c for c in d["controls"]) and any("fume hood" in c.lower() for c in d["controls"])
    assert {"GHS02", "GHS08"} <= set(d["pictograms"])
    # the assessment section keeps a snapshot of what was known when it was written
    sec = next(e for e in entries if e["kind"] == "safety")
    snap = {"taken_at": "2026-10-17T12:00:00Z", "chemicals": [{"name": "DMF", "signal": "Danger", "hazards": ["H360D May damage the unborn child"], "source": "EU CLP"}],
            "warnings": d["warnings"][:1]}
    u = client.put(f"/api/entries/{sec['id']}", headers=H, json={"content": {"controls": "Fume hood", "waste": "Halogen-free", "emergency": "", "snapshot": snap}})
    assert u.status_code == 200 and u.json()["content"]["snapshot"]["chemicals"][0]["name"] == "DMF"
    html = client.get(f"/api/experiments/{exp['id']}/export", headers=H).text
    assert "H360D" in html and "Halogen-free" in html


def test_green_metrics_and_scale_up(client, H):
    nb = client.post("/api/notebooks", headers=H, json={"name": "Green NB"}).json()
    exp = client.post("/api/experiments", headers=H, json={"title": "Suzuki", "notebook_id": nb["id"]}).json()
    rxn = next(e for e in client.get(f"/api/experiments/{exp['id']}", headers=H).json()["entries"] if e["kind"] == "reaction")
    rows = [{"role": "reactant", "name": "4-bromoanisole", "smiles": "COc1ccc(Br)cc1", "basis": "mass", "mass_mg": 187.0, "limiting": True},
            {"role": "reactant", "name": "PhB(OH)2", "smiles": "OB(O)c1ccccc1", "basis": "equiv", "equiv": 1.2},
            {"role": "solvent", "name": "dioxane", "smiles": "C1COCCO1", "basis": "volume", "volume_ml": 4.0, "density": 1.033},
            {"role": "product", "name": "4-methoxybiphenyl", "smiles": "COc1ccc(-c2ccccc2)cc1", "actual_mass_mg": 158.2}]
    c = client.put(f"/api/entries/{rxn['id']}", headers=H, json={"content": {"rows": rows, "conditions": {}}}).json()["content"]
    m = c["totals"]["metrics"]
    # AE = 184.24 / (187.04 + 121.93) = 59.6 %
    assert m["atom_economy_pct"] == pytest.approx(59.6, abs=0.1)
    inputs = 187.0 + 1.2 * 121.93 + 4.0 * 1033
    assert m["pmi"] == pytest.approx(inputs / 158.2, abs=0.1) and m["e_factor"] == pytest.approx(inputs / 158.2 - 1, abs=0.1)
    assert m["solvent_l_per_kg"] == pytest.approx(4.0 / 0.1582, abs=1)
    rx = client.get("/api/reactions", headers=H).json()
    assert next(r for r in rx if r["experiment_id"] == exp["id"])["metrics"]["atom_economy_pct"] == m["atom_economy_pct"]

    big = client.post(f"/api/experiments/{exp['id']}/clone", headers=H, params={"scale": 5}).json()
    assert big["title"].startswith("×5 scale")
    brx = next(e for e in client.get(f"/api/experiments/{big['id']}", headers=H).json()["entries"] if e["kind"] == "reaction")["content"]
    lim = next(r for r in brx["rows"] if r["limiting"])
    assert lim["mass_mg"] == pytest.approx(935.0) and brx["totals"]["limiting_mmol"] == pytest.approx(5 * c["totals"]["limiting_mmol"], rel=1e-3)
    assert next(r for r in brx["rows"] if r["role"] == "solvent")["volume_ml"] == pytest.approx(20.0)
    prod = next(r for r in brx["rows"] if r["role"] == "product")
    assert prod["actual_mass_mg"] is None and prod["theoretical_mass_mg"] == pytest.approx(5 * 184.24, rel=1e-3)
    assert client.post(f"/api/experiments/{exp['id']}/clone", headers=H, params={"scale": 0}).status_code == 400


def test_compounds_and_druglikeness(client, H):
    rows = client.get("/api/compounds", headers=H).json()
    biaryl = next(r for r in rows if r["formula"] == "C13H12O")
    assert biaryl["lipinski_violations"] == [] and 0 < biaryl["qed"] <= 1 and biaryl["best_yield"]
    p = client.post("/api/chem/properties", headers=H, json={"structure": "C" * 40}).json()
    assert "cLogP > 5" in p["lipinski_violations"] and "rotatable bonds > 10" in p["veber_violations"]


def test_screen_section(client, H):
    nb = client.post("/api/notebooks", headers=H, json={"name": "Screen NB"}).json()
    exp = client.post("/api/experiments", headers=H, json={"title": "Screen", "notebook_id": nb["id"]}).json()
    e = client.post(f"/api/experiments/{exp['id']}/entries", headers=H, json={"kind": "screen", "title": "Base × temperature"}).json()
    assert e["content"]["x_levels"] == ["25", "50", "80"]
    body = {"x_name": "Base", "y_name": "T (°C)", "x_levels": ["K2CO3", "Cs2CO3"], "y_levels": ["60", "80"], "response": "Yield (%)",
            "cells": {"0,0": {"value": "41"}, "1,1": {"value": 88, "experiment_id": exp["id"]}, "5,5": {"value": 1}, "junk": {}}}
    c = client.put(f"/api/entries/{e['id']}", headers=H, json={"content": body}).json()["content"]
    assert c["cells"] == {"0,0": {"value": 41.0, "note": ""}, "1,1": {"value": 88.0, "note": "", "experiment_id": exp["id"]}}
    md = client.get(f"/api/experiments/{exp['id']}/export", headers=H, params={"format": "md"}).text
    assert "| 80 | | 88 |" in md or "| 80 |  | 88 |" in md
