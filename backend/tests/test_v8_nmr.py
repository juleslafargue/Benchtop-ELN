"""v0.8: NMR shift prediction from NMRShiftDB2-format data, and the updater."""
from pathlib import Path

import httpx
import pytest
from rdkit import Chem

from app import nmrdata, nmrpredict

# A tiny stand-in for nmrshiftdb2withsignals.sd: same field layout ("shift;mult;atomIndex|", 0-based,
# 1H shifts on the carrying atom). Shift values are typical literature values in CDCl3.
FIXTURE = [
    ("CC(C)Cc1ccccc1", {"13C": {0: 22.4, 1: 30.3, 2: 22.4, 3: 45.5, 4: 141.6, 5: 129.1, 6: 128.1, 7: 125.7, 8: 128.1, 9: 129.1},
                        "1H": {0: 0.90, 1: 1.86, 2: 0.90, 3: 2.47, 5: 7.13, 6: 7.25, 7: 7.17, 8: 7.25, 9: 7.13}}),
    ("CC(C(=O)O)c1ccccc1", {"13C": {0: 18.1, 1: 45.4, 2: 181.0, 5: 139.7, 6: 127.6, 7: 128.7, 8: 127.4, 9: 128.7, 10: 127.6},
                            "1H": {0: 1.51, 1: 3.73, 4: 11.5, 6: 7.30, 7: 7.33, 8: 7.27, 9: 7.33, 10: 7.30}}),
    ("Cc1ccccc1", {"13C": {0: 21.4, 1: 137.9, 2: 129.0, 3: 128.2, 4: 125.3, 5: 128.2, 6: 129.0},
                   "1H": {0: 2.35, 2: 7.17, 3: 7.25, 4: 7.15, 5: 7.25, 6: 7.17}}),
    ("CCO", {"13C": {0: 18.4, 1: 58.3}, "1H": {0: 1.22, 1: 3.69, 2: 2.6}}),
    ("Cc1ccc(C)cc1", {"13C": {0: 21.0, 1: 134.7, 2: 129.0, 3: 129.0, 4: 134.7, 5: 21.0, 6: 129.0, 7: 129.0},
                      "1H": {0: 2.31, 2: 7.05, 3: 7.05, 5: 2.31, 6: 7.05, 7: 7.05}}),
]


def write_fixture(path, copies=2):
    """Each record twice, as two separate measurements, so environments have more than one example."""
    w = Chem.SDWriter(str(path))
    for _ in range(copies):
        for smi, spectra in FIXTURE:
            m = Chem.MolFromSmiles(smi)
            for nuc, shifts in spectra.items():
                m.SetProp(f"Spectrum {nuc} 0", "".join(f"{v};0.0Q;{i}|" for i, v in shifts.items()))
            w.write(m)
    w.close()


@pytest.fixture(scope="module")
def no_bundled():
    real = nmrpredict.BUNDLED
    nmrpredict.BUNDLED = Path("/nonexistent/nmr_shifts.sqlite")
    nmrpredict.user_table().unlink(missing_ok=True)
    nmrpredict.reset_cache()
    yield
    nmrpredict.BUNDLED = real
    nmrpredict.user_table().unlink(missing_ok=True)
    nmrpredict.reset_cache()


@pytest.fixture(scope="module")
def table(no_bundled, tmp_path_factory):
    sd = tmp_path_factory.mktemp("nmr") / "nmrshiftdb2withsignals.sd"
    write_fixture(sd)
    return nmrpredict.build([sd], nmrpredict.user_table(), "NMRShiftDB2 fixture")


def test_status_and_409_before_data(client, users, no_bundled):
    nmrpredict.user_table().unlink(missing_ok=True)
    nmrpredict.reset_cache()
    h = users["alice"]
    assert client.get("/api/nmr/status", headers=h).json()["available"] is False
    assert client.get("/api/nmr/predict", headers=h, params={"smiles": "CCO"}).status_code == 409
    assert client.post("/api/nmr/update", headers=h).status_code == 403  # admins only


def test_environments_are_stable_and_symmetric():
    m = Chem.MolFromSmiles("Cc1ccc(C)cc1")
    e = nmrpredict.environments(m)
    assert e[0] == e[5] and e[2] == e[3] == e[6] == e[7] and e[1] == e[4]
    assert len(e[0]) == nmrpredict.MAX_R + 1
    assert nmrpredict.environments(Chem.MolFromSmiles("c1(C)ccc(C)cc1"))[1] == e[0]  # atom order doesn't matter


def test_build_and_predict_ibuprofen(client, users, table):
    assert int(table["molecules"]) == 10 and int(table["assignments_13C"]) > 40
    h = users["alice"]
    st = client.get("/api/nmr/status", headers=h).json()
    assert st["available"] and st["meta"]["source"] == "NMRShiftDB2 fixture" and st["has_1H"] and st["age_days"] == 0
    ibu = "CC(C)Cc1ccc(cc1)C(C)C(=O)O"
    c = client.get("/api/nmr/predict", headers=h, params={"smiles": ibu, "nucleus": "13C"}).json()
    assert c["total"] == 10 and c["covered"] == 10  # 13 carbons, 10 symmetry-distinct
    shifts = sorted(g["shift"] for g in c["groups"])
    assert shifts[-1] == pytest.approx(181.0, abs=1.0)        # COOH
    assert shifts[0] == pytest.approx(18.1, abs=1.5)          # alpha CH3
    iso_me = next(g for g in c["groups"] if len(g["atoms"]) == 2 and g["shift"] < 30)
    assert iso_me["shift"] == pytest.approx(22.4, abs=1.0)
    p = client.get("/api/nmr/predict", headers=h, params={"smiles": ibu, "nucleus": "1H"}).json()
    assert p["total"] == 8 and sum(g["nH"] for g in p["groups"]) == 18
    acid = next(g for g in p["groups"] if g["exchangeable"])
    assert acid["nH"] == 1 and acid["shift"] == pytest.approx(11.5, abs=0.5)
    me6 = next(g for g in p["groups"] if g["nH"] == 6)
    assert me6["shift"] == pytest.approx(0.90, abs=0.15) and me6["vicinal_h"] == 1   # doublet
    ch2 = next(g for g in p["groups"] if g["nH"] == 2 and not g["aromatic"])
    assert ch2["shift"] == pytest.approx(2.47, abs=0.2)
    assert all(g["confidence"] in ("high", "medium", "low") for g in p["groups"])


def test_unknown_environment_is_reported_not_guessed(client, users, table):
    r = client.get("/api/nmr/predict", headers=users["alice"], params={"smiles": "FC(F)(F)S(=O)(=O)Cl", "nucleus": "13C"}).json()
    assert r["groups"][0]["shift"] is None and r["groups"][0]["confidence"] == "none" and r["covered"] == 0
    assert client.get("/api/nmr/predict", headers=users["alice"], params={"smiles": "nonsense"}).status_code == 422
    assert client.get("/api/nmr/predict", headers=users["alice"], params={"smiles": "CCO", "nucleus": "31P"}).status_code == 422


def test_salt_uses_main_fragment_and_indices_match_smiles(client, users, table):
    r = client.get("/api/nmr/predict", headers=users["alice"], params={"smiles": "[Na+].CC(C(=O)[O-])c1ccccc1", "nucleus": "13C"}).json()
    assert "Na" not in r["smiles"]
    m = Chem.MolFromSmiles(r["smiles"])
    assert all(m.GetAtomWithIdx(i).GetSymbol() == "C" for g in r["groups"] for i in g["atoms"])


def test_reads_records_that_point_at_hydrogens():
    m = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    hs = [a.GetIdx() for a in m.GetAtoms() if a.GetAtomicNum() == 1 and a.GetNeighbors()[0].GetIdx() == 0]
    m.SetProp("Spectrum 1H 0", f"1.22;0.0T;{hs[0]}|9.9;0.0S;999|bad|55;0.0S;1|")
    assert list(nmrpredict.iter_observations(m)) == [(1, 0, 1.22)]  # out-of-range index, junk and absurd shift skipped


def test_depict_atom_highlight_and_numbers(client):
    r = client.get("/api/chem/depict", params={"smiles": "CC(C)Cc1ccccc1", "atoms": "0,2", "numbers": True})
    assert r.status_code == 200 and "<svg" in r.text
    assert client.get("/api/chem/depict", params={"smiles": "CCO", "atoms": "x,99,-1"}).status_code == 200


def test_bundled_table_is_real_and_licensed():
    real = Path(nmrpredict.__file__).parent / "data"
    assert (real / "nmr_shifts.sqlite").stat().st_size > 1_000_000
    assert "nmrshiftdb2 Data License" in (real / "NMRSHIFTDB2_LICENSE.md").read_text()
    p = nmrpredict.Predictor(real / "nmr_shifts.sqlite")
    assert int(p.meta["assignments_13C"]) > 100_000
    # anisole's OCH3 and ipso carbon from 36k real structures
    m = Chem.MolFromSmiles("COc1ccccc1")
    e = nmrpredict.environments(m)
    assert p.lookup(13, e[0])["shift"] == pytest.approx(55.1, abs=1.5)
    assert p.lookup(13, e[2])["shift"] == pytest.approx(159.6, abs=2.0)


def test_updater_downloads_only_when_newer_and_survives_bad_downloads(tmp_path, monkeypatch, no_bundled):
    sd = tmp_path / "x.sd"
    write_fixture(sd)
    body = sd.read_bytes()
    served = {"modified": "Fri, 10 Oct 2026 10:00:00 GMT", "html": False, "gets": 0}

    def handler(req):
        if req.method == "HEAD":
            return httpx.Response(200, headers={"last-modified": served["modified"]})
        served["gets"] += 1
        return httpx.Response(200, content=b"<!DOCTYPE html><html>mirror page</html>" if served["html"] else body)
    real_client = httpx.Client
    monkeypatch.setattr(nmrdata.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(nmrdata, "STATE", tmp_path / "state.json")

    r = nmrdata.update()
    assert r["updated"] and served["gets"] == 1 and nmrpredict.status()["available"]
    assert nmrdata.update()["updated"] is False and served["gets"] == 1   # unchanged upstream: no download
    served.update(modified="Sat, 17 Oct 2026 10:00:00 GMT", html=True)
    with pytest.raises(RuntimeError):
        nmrdata.update()                                                   # web page instead of data
    assert nmrpredict.status()["available"] and "web page" in nmrpredict.status()["last_error"]  # old table kept
    r = nmrdata.update(file=sd)                                            # manual file always works
    assert r["updated"]


def test_raw_fid_processing_choices_are_saved_and_cleaned(client, users, notebook):
    from tests.test_v2 import new_exp
    h = users["alice"]
    ch = next(e for e in new_exp(client, h, notebook)["entries"] if e["kind"] == "characterization")

    def save(fid):
        r = client.put(f"/api/entries/{ch['id']}", headers=h, json={"content": {"items": [
            {"technique": "1H NMR", "data": "", "analysis": {"integrals": [[1.0, 0.9]], "fid": fid}}]}})
        return r.json()["content"]["items"][0]["analysis"].get("fid")
    assert save({"ph0": 400, "ph1": -52.123, "lb": 0.3}) == {"ph0": 40.0, "ph1": -52.12, "lb": 0.3}
    assert save({"ph0": "NaN", "ph1": 1, "lb": 0.3}) is None        # junk phase isn't stored
    assert save("rubbish") is None
