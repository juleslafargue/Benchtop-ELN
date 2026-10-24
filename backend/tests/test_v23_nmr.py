"""0.23: the two protons of a terminal =CH2 (styrene, acrylates, allyl groups) are two environments, and a blank
structure gets a plain message.

A =CH2 whose other end carries two different groups has one proton cis and one trans to the substituent: two
signals (styrene 5.24 and 5.75). They were one 2H group, so the spectrum check said "5 environments expected, 6
signals found" with a warning, for every vinyl compound. Like a CH2 next to a stereocentre, they are now predicted
as two 1H halves and counted as two environments. Isobutylene's =CH2 (two identical groups at the other end) stays one.
"""
from __future__ import annotations

from rdkit import Chem

from app import nmrpredict


def envs(client, h, smiles):
    r = client.get("/api/chem/structure-check", headers=h, params={"smiles": smiles})
    assert r.status_code == 200, r.text
    return sorted(e["count"] for e in r.json()["h_environments"])


def test_terminal_alkene_ch2_protons_are_two_environments(client, admin):
    assert nmrpredict.geminal_alkene_ch2(Chem.MolFromSmiles("C=Cc1ccccc1")) == {0}
    assert nmrpredict.geminal_alkene_ch2(Chem.MolFromSmiles("C=C(C)C")) == set()        # isobutylene
    assert nmrpredict.geminal_alkene_ch2(Chem.MolFromSmiles("C=CC(=O)OCC=C")) == {0, 7}  # allyl acrylate
    assert nmrpredict.geminal_alkene_ch2(Chem.MolFromSmiles("CC=CC")) == set()           # no =CH2
    assert envs(client, admin, "C=Cc1ccccc1") == [1, 1, 1, 1, 2, 2]  # styrene: o, m, p, =CH, and the two =CH2 protons
    assert envs(client, admin, "C=C(C)C") == [2, 6]
    assert envs(client, admin, "CC(C)Cc1ccc(cc1)C(C)C(=O)O") == [1, 1, 1, 2, 2, 2, 3, 6]  # ibuprofen: as before
    assert envs(client, admin, "OC(CC)(CC)CC") == [1, 6, 9]  # no stereocentre, no alkene: as before
    # a CH2 next to a stereocentre, and symmetry-equivalent CH2s keep their halves together
    assert envs(client, admin, "CC[C@H](C)O") == [1, 1, 1, 1, 3, 3]
    assert envs(client, admin, "C=CC(C=C)(C)C") == [2, 2, 2, 6]

    g = client.get("/api/nmr/predict", headers=admin, params={"smiles": "C=Cc1ccccc1", "nucleus": "1H"}).json()["groups"]
    halves = [x for x in g if x.get("diastereotopic")]
    assert sorted(x["diastereotopic"] for x in halves) == ["a", "b"] and all(x["nH"] == 1 and x["atoms"] == [0] for x in halves)
    assert sum(x["nH"] for x in g) == 8


def test_blank_or_broken_structures_get_a_plain_message(client, admin):
    for s in ("", "   ", "C1CC", "not a structure"):
        r = client.get("/api/nmr/predict", headers=admin, params={"smiles": s, "nucleus": "1H"})
        assert r.status_code == 422 and r.json()["detail"] == "Couldn't read that structure", (s, r.text)
