"""Load demo data:  python -m app.seed   (or ./benchtop seed, or the option on the first-run screen)

Creates users alice (author) and bob (witness), both with password 'benchtop123',
a reagent registry with lots, a notebook, a template and two worked Suzuki couplings.
It does nothing if the demo users already exist.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import warnings

with warnings.catch_warnings():  # the demo builder drives the API in-process; the library's deprecation notice isn't for users
    warnings.simplefilter("ignore", DeprecationWarning)
    from fastapi.testclient import TestClient
from sqlmodel import Session, select

from . import audit
from .db import engine
from .main import app
from .models import GroupMember, LabGroup, User
from .security import hash_password

PW = "benchtop123"


def _create_demo_users() -> bool:
    with Session(engine) as s:
        if s.exec(select(User).where(User.username == "alice")).first():
            return False
        ids = {}
        for u, n in (("alice", "Alice Chen"), ("bob", "Bob Okafor")):
            user = User(username=u, full_name=n, email=f"{u}@example.org", password_hash=hash_password(PW))
            s.add(user)
            s.flush()
            ids[u] = user.id
            audit.record(s, None, "create", "user", user.id, username=u, role="scientist", demo=True)
        # a demo research group: Alice leads it, Bob is a member
        if not s.exec(select(LabGroup).where(LabGroup.name == "Chen group")).first():
            g = LabGroup(name="Chen group", description="Demo research group: methodology and cross-coupling")
            s.add(g)
            s.flush()
            s.add(GroupMember(group_id=g.id, user_id=ids["alice"], role="lead"))
            s.add(GroupMember(group_id=g.id, user_id=ids["bob"], role="member"))
            audit.record(s, None, "create", "group", g.id, name=g.name, demo=True)
        s.commit()
    return True


def load_demo(c) -> bool:
    """Populate demo content through the API (so it is validated and audited like real work)."""
    if not _create_demo_users():
        return False
    tok = c.post("/api/auth/login", json={"username": "alice", "password": PW}).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}

    mats = {}
    for name, smi, cas, kind, dens, lots in [
        ("4-Bromoanisole", "COc1ccc(Br)cc1", "104-92-7", "reagent", 1.494, [(25, "g", "Shelf A2", "Sigma-Aldrich", 99)]),
        ("Phenylboronic acid", "OB(O)c1ccccc1", "98-80-6", "reagent", None, [(10, "g", "Shelf A3", "Combi-Blocks", 97)]),
        ("Potassium carbonate", "[K+].[K+].[O-]C([O-])=O", "584-08-7", "reagent", None, [(500, "g", "Cabinet B1", "Fisher", 99)]),
        ("Tetrakis(triphenylphosphine)palladium(0)",
         "c1ccc(cc1)P(c1ccccc1)c1ccccc1.c1ccc(cc1)P(c1ccccc1)c1ccccc1.c1ccc(cc1)P(c1ccccc1)c1ccccc1.c1ccc(cc1)P(c1ccccc1)c1ccccc1.[Pd]",
         "14221-01-3", "reagent", None, [(1, "g", "Glovebox", "Strem", 99)]),
        ("1,4-Dioxane", "C1COCCO1", "123-91-1", "solvent", 1.033, [(1, "L", "Solvent cabinet", "Fisher", 99.8)]),
        ("Benzaldehyde", "O=Cc1ccccc1", "100-52-7", "reagent", 1.044, [(100, "mL", "Shelf A1", "Sigma-Aldrich", 99)]),
        ("Sodium borohydride", "[Na+].[BH4-]", "16940-66-2", "reagent", None, [(25, "g", "Desiccator", "Sigma-Aldrich", 98)]),
        ("Methanol", "CO", "67-56-1", "solvent", 0.792, [(2.5, "L", "Solvent cabinet", "Fisher", 99.9)]),
    ]:
        m = c.post("/api/materials", headers=H, json={"name": name, "smiles": smi, "cas": cas,
                                                      "kind": kind, "density": dens}).json()
        mats[name] = m
        for amt, unit, loc, sup, pur in lots:
            body = {"amount": amt, "unit": unit, "location": loc, "supplier": sup, "purity": pur}
            if name == "Sodium borohydride":  # so the dashboard's inventory widget has an expiry to show
                body["expires_at"] = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=21)).replace(microsecond=0).isoformat()
            lot = c.post(f"/api/materials/{m['id']}/lots", headers=H, json=body).json()
            m.setdefault("lot_ids", []).append(lot["id"])

    grp = next((g for g in c.get("/api/groups", headers=H).json() if g["name"] == "Chen group"), None)
    nb = c.post("/api/notebooks", headers=H, json={"name": "Cross-coupling methods",
                                                   "description": "Pd-catalysed C–C bond formation",
                                                   **({"group_id": grp["id"], "visibility": "group"} if grp else {})}).json()

    tpl = c.post("/api/experiments", headers=H, json={"title": "General synthesis", "is_template": True,
                                                      "description": "Standard layout for a single-step synthesis"}).json()
    tpl_full = c.get(f"/api/experiments/{tpl['id']}", headers=H).json()
    tents = {e["title"]: e for e in tpl_full["entries"]}
    c.put(f"/api/entries/{tents['Procedure']['id']}", headers=H, json={"content": {"html":
          "<ol><li>Charge flask with limiting reagent and solvent.</li><li>Add reagents.</li>"
          "<li>Heat / stir; monitor by TLC.</li><li>Work-up.</li><li>Purify.</li></ol>"}})
    c.put(f"/api/entries/{tents['Characterisation']['id']}", headers=H, json={"content": {"items": [
        {"technique": t, "conditions": cnd, "data": ""} for t, cnd in
        (("1H NMR", "400 MHz, CDCl3"), ("13C NMR", "101 MHz, CDCl3"), ("HRMS", "ESI+"), ("mp", ""))]}})

    def mrow(name, role, **kw):
        m = mats[name]
        return {"role": role, "name": name, "smiles": m["smiles"], "material_id": m["id"],
                "lot_id": m["lot_ids"][0], "density": m.get("density"), **kw}

    def suzuki(title, aryl_mass, temp, time_h, conc, cat_eq, product_mg, tags, procedure, nmr=False):
        exp = c.post("/api/experiments", headers=H, json={
            "title": title, "notebook_id": nb["id"], "template_id": tpl["id"], "tags": tags,
            "description": "Synthesis of 4-methoxybiphenyl, 1 mmol scale."}).json()
        full = c.get(f"/api/experiments/{exp['id']}", headers=H).json()
        ents = {e["title"]: e for e in full["entries"]}
        c.put(f"/api/entries/{ents['Aim']['id']}", headers=H, json={"content": {"html":
              "<p>Prepare 4-methoxybiphenyl via Pd(PPh<sub>3</sub>)<sub>4</sub>-catalysed Suzuki–Miyaura coupling "
              "as a reference for the substrate-scope study.</p>"}})
        rows = [
            mrow("4-Bromoanisole", "reactant", basis="mass", mass_mg=aryl_mass, limiting=True),
            mrow("Phenylboronic acid", "reactant", basis="equiv", equiv=1.2, purity=97),
            mrow("Potassium carbonate", "reagent", basis="equiv", equiv=2.0),
            mrow("Tetrakis(triphenylphosphine)palladium(0)", "catalyst", basis="equiv", equiv=cat_eq),
            mrow("1,4-Dioxane", "solvent", basis="fill"),
            {"role": "product", "name": "4-Methoxybiphenyl", "smiles": "COc1ccc(-c2ccccc2)cc1",
             "actual_mass_mg": product_mg, "purity": 98},
        ]
        c.put(f"/api/entries/{ents['Reaction']['id']}", headers=H, json={"content": {
            "reaction_smiles": "COc1ccc(Br)cc1.OB(O)c1ccccc1>>COc1ccc(-c2ccccc2)cc1", "rows": rows,
            "conditions": {"temperature_c": temp, "time_h": time_h, "atmosphere": "N2", "target_conc_m": conc}}})
        c.put(f"/api/entries/{ents['Procedure']['id']}", headers=H, json={"content": {"html": procedure}})
        c.put(f"/api/entries/{ents['Work-up & purification']['id']}", headers=H, json={"content": {
            "method": "column", "stationary_phase": "Silica gel (40–63 µm)", "eluent": "5% EtOAc/hexanes",
            "rf": "0.45", "rf_eluent": "10% EtOAc/hexanes", "isolated_mg": product_mg, "appearance": "White solid",
            "notes_html": "<p>Diluted with EtOAc, washed with water and brine, dried (MgSO<sub>4</sub>), "
                          "concentrated, then loaded onto silica.</p>"}})
        items = [
            {"technique": "1H NMR", "conditions": "400 MHz, CDCl3",
             "data": "δ 7.56–7.52 (m, 4H), 7.42 (t, J = 7.6 Hz, 2H), 7.30 (t, J = 7.4 Hz, 1H), 6.98 (d, J = 8.8 Hz, 2H), 3.86 (s, 3H)"},
            {"technique": "mp", "conditions": "", "data": "86–88 °C"},
            {"technique": "HRMS", "conditions": "ESI", "data": ""},
        ]
        if nmr:
            from .demo_spectra import METHOXYBIPHENYL, nmr_jcamp

            jdx = nmr_jcamp(METHOXYBIPHENYL, "4-methoxybiphenyl 1H NMR (CDCl3)")
            att = c.post(f"/api/experiments/{exp['id']}/attachments", headers=H,
                         files={"file": ("4-methoxybiphenyl_1H.jdx", jdx.encode(), "chemical/x-jcamp-dx")}).json()
            items[0]["attachment_id"] = att["id"]
            c.post(f"/api/experiments/{exp['id']}/entries", headers=H, json={
                "kind": "file", "title": "Spectra", "content": {"attachment_ids": [att["id"]], "caption": "Crude-free sample after column"}})
        c.put(f"/api/entries/{ents['Characterisation']['id']}", headers=H, json={"content": {"items": items}})
        c.put(f"/api/entries/{ents['Observations & results']['id']}", headers=H, json={"content": {"html":
              "<p>Reaction turned black after ~30 min. TLC showed full conversion at the end of the reaction.</p>"}})
        for name, amt in (("Phenylboronic acid", 150), ("4-Bromoanisole", aryl_mass)):
            c.post(f"/api/lots/{mats[name]['lot_ids'][0]}/transactions", headers=H, json={
                "amount": amt, "unit": "mg", "experiment_id": exp["id"], "note": f"used in {exp['eid']}"})
        return exp

    # earlier catalyst use, so the Pd lot shows up as running low on the dashboard
    c.post(f"/api/lots/{mats['Tetrakis(triphenylphosphine)palladium(0)']['lot_ids'][0]}/transactions", headers=H,
           json={"amount": 880, "unit": "mg", "note": "used in earlier screening (before Benchtop)"})
    suzuki("Suzuki coupling at 80 °C (screening)", 187.0, 80, 4, 0.1, 0.05, 101.3, ["suzuki", "biaryl", "screening"],
           "<p>As general procedure, 80 °C for 4 h. Incomplete conversion by TLC.</p>")
    suzuki("Suzuki coupling of 4-bromoanisole with phenylboronic acid", 187.0, 90, 16, 0.2, 0.03, 158.2,
           ["suzuki", "biaryl"],
           "<ol><li>A 25 mL Schlenk flask was charged with 4-bromoanisole (187 mg, 1.00 mmol), phenylboronic acid "
           "(1.2 equiv) and K<sub>2</sub>CO<sub>3</sub> (2.0 equiv).</li><li>Dioxane (to 0.2 M) was added and the "
           "mixture sparged with N<sub>2</sub> for 10 min.</li><li>Pd(PPh<sub>3</sub>)<sub>4</sub> (3 mol%) was added and the "
           "mixture heated at 90 °C for 16 h.</li><li>Worked up and purified as below.</li></ol>", nmr=True)

    c.post("/api/experiments", headers=H, json={"title": "NaBH4 reduction of benzaldehyde",
                                                "notebook_id": nb["id"], "description": "Draft"})
    return True


def main() -> None:
    with TestClient(app) as c:
        if load_demo(c):
            print(f"Demo data loaded. Log in as alice / {PW} (author) or bob / {PW} (witness).")
        else:
            print("Demo data already present; nothing to do.")


if __name__ == "__main__":
    main()
