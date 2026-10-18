"""v0.12: blended, outlier-trimmed shift prediction; diastereotopic CH2; learning from the lab's confirmed assignments."""
import pytest
from rdkit import Chem

from app import nmrpredict
from tests.test_v2 import new_exp
from tests.test_v8_nmr import no_bundled, table, write_fixture  # noqa: F401  (fixtures)

PW = "lab-bench-pw-2026"


def test_trimmed_mean_ignores_a_misassignment():
    assert nmrpredict.trimmed_mean([128.1, 128.3, 128.0, 128.2, 172.0], 13) == pytest.approx(128.15, abs=0.01)
    assert nmrpredict.trimmed_mean([2.0, 3.0], 1) == 2.5


def test_blending_moves_toward_specific_spheres(table):  # noqa: F811
    p = nmrpredict.predictor()
    assert p.v2 and p.meta["format"] == "2"
    m = Chem.MolFromSmiles("CC(C)Cc1ccccc1")
    r = p.lookup(13, nmrpredict.environments(m)[3])  # the benzylic CH2, seen twice at every sphere
    assert r["radius"] == 4 and r["shift"] == pytest.approx(45.5, abs=0.3)


def test_old_format_tables_still_work(tmp_path):
    import sqlite3
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE stats (nuc INTEGER, r INTEGER, key INTEGER, n INTEGER, s REAL, ss REAL, mn REAL, mx REAL, PRIMARY KEY (nuc, r, key)) WITHOUT ROWID")
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    codes = nmrpredict.environments(Chem.MolFromSmiles("CCO"))[1]
    con.executemany("INSERT INTO stats VALUES (?,?,?,?,?,?,?,?)", [(13, r, codes[r], 2, 116.0, 116.0 ** 2 / 2, 57.9, 58.1) for r in range(1, 5)])
    con.execute("INSERT INTO meta VALUES ('format', '1')")
    con.commit(); con.close()
    assert nmrpredict.Predictor(db).lookup(13, codes)["shift"] == pytest.approx(58.0, abs=0.01)


def test_diastereotopic_ch2():
    assert nmrpredict.diastereotopic_ch2(Chem.MolFromSmiles("CC(O)CC(=O)O")) == {3}   # next to the CH(OH) stereocentre
    assert nmrpredict.diastereotopic_ch2(Chem.MolFromSmiles("CCO")) == set()
    assert nmrpredict.diastereotopic_ch2(Chem.MolFromSmiles("CC(C)Cc1ccc(cc1)C(C)C(=O)O")) == set()  # ibuprofen: stereocentre too far


def test_confirmed_assignments_teach_the_predictor_once_signed(client, users, table, notebook):  # noqa: F811
    a = users["alice"]
    before = client.get("/api/nmr/predict", headers=a, params={"smiles": "CCO", "nucleus": "1H"}).json()
    ch2 = next(g for g in before["groups"] if g["nH"] == 2)
    assert ch2["shift"] == pytest.approx(3.69, abs=0.05) and "lab_n" not in ch2
    exp = new_exp(client, a, notebook)
    analysis = {"integrals": [], "ref_idx": 0, "ref_value": 1, "threshold": 0.05,
                "assignments": {"nucleus": "1H", "smiles": "CCO", "solvent": "DMSO-d6", "confirmed_by": "Alice",
                                "items": [{"atoms": [1], "shift": 3.44}, {"atoms": [0], "shift": 1.06}, {"atoms": [99], "shift": 50}]}}
    r = client.post(f"/api/experiments/{exp['id']}/entries", headers=a, json={"kind": "characterization", "title": "Characterisation",
                                                                               "content": {"items": [{"technique": "1H NMR", "analysis": analysis}]}})
    saved = r.json()["content"]["items"][0]["analysis"]["assignments"]
    assert [it["atoms"] for it in saved["items"]] == [[1], [0]]  # the impossible shift was dropped
    # not signed yet: nothing learned
    assert "lab_n" not in next(g for g in client.get("/api/nmr/predict", headers=a, params={"smiles": "CCO", "nucleus": "1H"}).json()["groups"] if g["nH"] == 2)
    client.post(f"/api/experiments/{exp['id']}/sign", headers=a, json={"password": PW})
    after = client.get("/api/nmr/predict", headers=a, params={"smiles": "CCO", "nucleus": "1H"}).json()
    g = next(g for g in after["groups"] if g["nH"] == 2)
    assert g["lab_n"] == 1 and g["confidence"] == "high" and 3.44 <= g["shift"] < 3.60  # pulled firmly toward the lab's value
    assert after["source"]["lab_groups"] == 2
    assert client.get("/api/nmr/status", headers=a).json()["lab"] == {"shifts": 2, "experiments": 1}
    # withdrawing the signature takes it back out
    client.post(f"/api/experiments/{exp['id']}/withdraw", headers=a, json={"password": PW})
    g = next(g for g in client.get("/api/nmr/predict", headers=a, params={"smiles": "CCO", "nucleus": "1H"}).json()["groups"] if g["nH"] == 2)
    assert "lab_n" not in g and g["shift"] == pytest.approx(3.69, abs=0.05)
