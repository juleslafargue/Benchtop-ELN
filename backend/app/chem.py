"""Cheminformatics helpers built on RDKit.

Units used throughout the stoichiometry table (the same conventions as most chemistry ELNs):
    mass  mg      amount  mmol     volume  mL
    MW    g/mol   density g/mL     conc    mol/L (== mmol/mL)
"""
from __future__ import annotations

from typing import Any, Optional

from rdkit import Chem, DataStructs, RDLogger
from rdkit.Chem import AllChem, Descriptors, rdChemReactions, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

RDLogger.DisableLog("rdApp.*")


class ChemError(ValueError):
    pass


# ---------------------------------------------------------------- parsing / properties

def mol_from_any(text: str) -> Chem.Mol:
    """Accept SMILES, a molfile/MOL block, or an InChI string."""
    text = (text or "").strip()
    if not text:
        raise ChemError("empty structure")
    mol = None
    if "M  END" in text or "V2000" in text or "V3000" in text:
        mol = Chem.MolFromMolBlock(text, sanitize=True)
    elif text.startswith("InChI="):
        mol = Chem.MolFromInchi(text)
    else:
        mol = Chem.MolFromSmiles(text)
    if mol is None:
        raise ChemError("could not parse structure")
    return mol


def properties(text: str) -> dict[str, Any]:
    mol = mol_from_any(text)
    return {
        "smiles": Chem.MolToSmiles(mol),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "mw": round(Descriptors.MolWt(mol), 4),
        "exact_mass": round(Descriptors.ExactMolWt(mol), 5),
        "inchi": Chem.MolToInchi(mol) or "",
        "inchikey": Chem.MolToInchiKey(mol) or "",
        "hbd": rdMolDescriptors.CalcNumHBD(mol),
        "hba": rdMolDescriptors.CalcNumHBA(mol),
        "logp": round(Descriptors.MolLogP(mol), 2),
        "tpsa": round(rdMolDescriptors.CalcTPSA(mol), 2),
        "rotatable_bonds": rdMolDescriptors.CalcNumRotatableBonds(mol),
        "molblock": Chem.MolToMolBlock(mol),
    }


def depict_svg(text: str, width: int = 300, height: int = 200) -> str:
    mol = mol_from_any(text)
    AllChem.Compute2DCoords(mol)
    d = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = d.drawOptions()
    opts.clearBackground = False
    opts.bondLineWidth = 1.5
    d.DrawMolecule(mol)
    d.FinishDrawing()
    return d.GetDrawingText()


def depict_reaction_svg(rxn_smiles: str, width: int = 700, height: int = 200) -> str:
    rxn = _reaction(rxn_smiles)
    d = rdMolDraw2D.MolDraw2DSVG(width, height)
    d.drawOptions().clearBackground = False
    d.DrawReaction(rxn)
    d.FinishDrawing()
    return d.GetDrawingText()


def _reaction(text: str):
    text = (text or "").strip()
    try:
        if "$RXN" in text:
            rxn = rdChemReactions.ReactionFromRxnBlock(text)
        else:
            rxn = rdChemReactions.ReactionFromSmarts(text, useSmiles=True)
    except Exception as exc:  # RDKit raises ValueError subclasses inconsistently
        raise ChemError(f"could not parse reaction: {exc}") from exc
    if rxn is None:
        raise ChemError("could not parse reaction")
    return rxn


def _merge_salts(mols) -> list[Chem.Mol]:
    """Reaction SMILES writes NaBH4 as '[Na+].[BH4-]', i.e. two components.
    Recombine adjacent charged fragments until their net charge is zero."""
    out: list[Chem.Mol] = []
    pending: Optional[Chem.Mol] = None
    charge = 0
    for m in mols:
        q = Chem.GetFormalCharge(m)
        if pending is None:
            if q == 0:
                out.append(m)
            else:
                pending, charge = m, q
            continue
        if q == 0:  # neutral molecule between ions: keep it separate
            out.append(m)
            continue
        pending = Chem.CombineMols(pending, m)
        charge += q
        if charge == 0:
            out.append(pending)
            pending = None
    if pending is not None:
        out.append(pending)
    return out


def parse_reaction(text: str) -> dict[str, Any]:
    """Split a reaction (SMILES 'A.B>C>D' or RXN block) into stoichiometry rows."""
    rxn = _reaction(text)
    rows: list[dict[str, Any]] = []

    def add(mol: Chem.Mol, role: str) -> None:
        try:
            Chem.SanitizeMol(mol)
        except Exception:
            pass
        smi = Chem.MolToSmiles(mol)
        try:
            p = properties(smi)
            rows.append(new_row(role=role, smiles=p["smiles"], mw=p["mw"], formula=p["formula"]))
        except ChemError:
            rows.append(new_row(role=role, smiles=smi))

    for m in _merge_salts(rxn.GetReactants()):
        add(m, "reactant")
    for m in _merge_salts(rxn.GetAgents()):
        add(m, "reagent")
    for m in _merge_salts(rxn.GetProducts()):
        add(m, "product")
    if rows and not any(r["limiting"] for r in rows):
        for r in rows:
            if r["role"] == "reactant":
                r["limiting"] = True
                break
    return {"rows": rows, "reaction_smiles": rdChemReactions.ReactionToSmiles(rxn)}


# ---------------------------------------------------------------- stoichiometry

ROLES = ("reactant", "reagent", "catalyst", "solvent", "product")
BASES = ("mass", "volume", "equiv", "mmol", "fill")  # fill: solvent volume set by target concentration


def new_row(**kw: Any) -> dict[str, Any]:
    row = {
        "role": "reactant", "name": "", "smiles": "", "formula": "", "mw": None,
        "material_id": None, "lot_id": None,
        "equiv": None, "mmol": None, "mass_mg": None, "volume_ml": None,
        "density": None, "conc_m": None, "purity": 100.0,
        "limiting": False, "basis": "equiv",
        "actual_mass_mg": None, "theoretical_mass_mg": None, "yield_pct": None,
        "notes": "",
    }
    row.update(kw)
    return row


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _r(v: Optional[float], nd: int = 4) -> Optional[float]:
    return None if v is None else round(v, nd)


def compute_stoichiometry(rows_in: list[dict[str, Any]], target_conc_m: Any = None) -> dict[str, Any]:
    """Fill in derived quantities for a reaction table.

    Each row carries a `basis` saying which quantity the chemist typed:
      mass   -> mmol = mass * purity / MW
      volume -> mass = V * density (neat liquid) or mmol = V * conc (solution)
      mmol   -> taken as-is
      equiv  -> mmol = equiv * mmol(limiting reagent)
    The limiting row (exactly one) defines 1.0 equiv; products get theoretical and
    percent yield from the limiting amount.

    With `target_conc_m` (mol/L, relative to the limiting reagent), solvent rows whose basis
    is "fill" get whatever volume brings the total to that concentration, split evenly.
    """
    rows = [new_row(**{k: v for k, v in r.items()}) for r in rows_in]
    warnings: list[str] = []

    for r in rows:
        if r["role"] not in ROLES:
            r["role"] = "reactant"
        if r["basis"] not in BASES:
            r["basis"] = "equiv"
        if r["smiles"] and not _f(r["mw"]):
            try:
                p = properties(r["smiles"])
                r["mw"], r["formula"] = p["mw"], p["formula"]
                r["smiles"] = p["smiles"]
            except ChemError:
                warnings.append(f"Could not parse structure for '{r['name'] or r['smiles']}'")

    limiting = [r for r in rows if r["limiting"] and r["role"] != "product"]
    if len(limiting) > 1:
        warnings.append("More than one limiting reagent flagged; using the first")
        for r in limiting[1:]:
            r["limiting"] = False
        limiting = limiting[:1]
    if not limiting:
        for r in rows:
            if r["role"] == "reactant":
                r["limiting"] = True
                limiting = [r]
                break

    def resolve_direct(r: dict[str, Any]) -> Optional[float]:
        """mmol from the row's own inputs (not via equivalents)."""
        mw, purity = _f(r["mw"]), (_f(r["purity"]) or 100.0) / 100.0
        dens, conc = _f(r["density"]), _f(r["conc_m"])
        mass, vol, mmol = _f(r["mass_mg"]), _f(r["volume_ml"]), _f(r["mmol"])
        b = r["basis"]
        if b == "mmol" and mmol is not None:
            return mmol
        if b == "volume" and vol is not None:
            if conc:
                return vol * conc
            if dens:
                mass = vol * dens * 1000.0
                r["mass_mg"] = mass
                return mass * purity / mw if mw else None
            return None
        if b == "mass" and mass is not None and mw:
            return mass * purity / mw
        return None

    lim_mmol: Optional[float] = None
    if limiting:
        lr = limiting[0]
        if lr["basis"] == "equiv":  # equiv is meaningless for the reference row
            lr["basis"] = "mass" if _f(lr["mass_mg"]) else ("mmol" if _f(lr["mmol"]) else "mass")
        lim_mmol = resolve_direct(lr)
        if lim_mmol is None:
            warnings.append("Enter an amount (mass, volume or mmol) for the limiting reagent")

    for r in rows:
        if r["role"] == "solvent":
            # solvents: only volume (and mass if density) are meaningful
            vol, dens = _f(r["volume_ml"]), _f(r["density"])
            r["mass_mg"] = _r(vol * dens * 1000.0, 2) if (vol and dens) else _f(r["mass_mg"])
            r["equiv"] = r["mmol"] = None
            continue

        mw, purity = _f(r["mw"]), (_f(r["purity"]) or 100.0) / 100.0
        dens, conc = _f(r["density"]), _f(r["conc_m"])

        if r["role"] == "product":
            eq = _f(r["equiv"]) or 1.0
            r["equiv"] = eq
            if lim_mmol is not None:
                r["mmol"] = lim_mmol * eq
                if mw:
                    r["theoretical_mass_mg"] = _r(r["mmol"] * mw, 3)
            actual = _f(r["actual_mass_mg"])
            if actual is not None and _f(r["theoretical_mass_mg"]):
                r["yield_pct"] = _r(100.0 * actual * purity / r["theoretical_mass_mg"], 1)
            else:
                r["yield_pct"] = None
            r["mmol"] = _r(_f(r["mmol"]))
            continue

        if r["limiting"]:
            mmol = lim_mmol
        elif r["basis"] == "equiv":
            eq = _f(r["equiv"])
            mmol = eq * lim_mmol if (eq is not None and lim_mmol is not None) else None
        else:
            mmol = resolve_direct(r)

        r["mmol"] = mmol
        if mmol is not None:
            if lim_mmol:
                r["equiv"] = 1.0 if r["limiting"] else mmol / lim_mmol
            b = r["basis"]
            if b in ("equiv", "mmol"):
                if conc:
                    r["volume_ml"] = mmol / conc
                    r["mass_mg"] = mmol * mw / purity if mw else None
                elif mw:
                    r["mass_mg"] = mmol * mw / purity
                    r["volume_ml"] = r["mass_mg"] / dens / 1000.0 if dens else None
            elif b == "mass":
                r["volume_ml"] = (_f(r["mass_mg"]) / dens / 1000.0) if dens else None
            elif b == "volume" and conc:
                # solution: mass of solute delivered
                r["mass_mg"] = mmol * mw / purity if mw else None
        for k, nd in (("mmol", 4), ("equiv", 3), ("mass_mg", 3), ("volume_ml", 4)):
            r[k] = _r(_f(r[k]), nd)

    target = _f(target_conc_m)
    fill_rows = [r for r in rows if r["role"] == "solvent" and r["basis"] == "fill"]
    if fill_rows:
        if target and lim_mmol:
            needed = lim_mmol / target  # mmol / (mmol/mL) = mL
            fixed = sum(_f(r["volume_ml"]) or 0 for r in rows if r not in fill_rows and r["role"] != "product")
            remaining = needed - fixed
            if remaining <= 0:
                warnings.append(f"Other liquids already exceed {needed:.3g} mL; can't reach {target:g} M")
                remaining = 0.0
            for r in fill_rows:
                r["volume_ml"] = _r(remaining / len(fill_rows), 3)
                dens = _f(r["density"])
                r["mass_mg"] = _r(r["volume_ml"] * dens * 1000.0, 2) if dens else None
        elif not target:
            warnings.append("Set a target concentration to calculate the solvent volume")

    totals = {
        "limiting_mmol": _r(lim_mmol),
        "total_volume_ml": _r(sum(_f(r["volume_ml"]) or 0 for r in rows), 3),
    }
    if lim_mmol and totals["total_volume_ml"]:
        totals["concentration_m"] = _r(lim_mmol / totals["total_volume_ml"], 4)
    return {"rows": rows, "totals": totals, "warnings": warnings}


# ---------------------------------------------------------------- searching

def substructure_matcher(query: str):
    q = Chem.MolFromSmarts(query) or Chem.MolFromSmiles(query)
    if q is None:
        raise ChemError("invalid SMARTS/SMILES query")

    def match(smiles: str) -> bool:
        m = Chem.MolFromSmiles(smiles) if smiles else None
        return bool(m and m.HasSubstructMatch(q))

    return match


_fpgen = None


def _fp(mol: Chem.Mol):
    global _fpgen
    if _fpgen is None:
        from rdkit.Chem import rdFingerprintGenerator

        _fpgen = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    return _fpgen.GetFingerprint(mol)


def similarity_scorer(query: str):
    qfp = _fp(mol_from_any(query))

    def score(smiles: str) -> float:
        m = Chem.MolFromSmiles(smiles) if smiles else None
        return DataStructs.TanimotoSimilarity(qfp, _fp(m)) if m else 0.0

    return score


# ---------------------------------------------------------------- structure checks for spectra

def structure_check(text: str) -> dict[str, Any]:
    """Topologically distinct carbons and proton environments (symmetry from canonical ranks).

    Diastereotopic protons are not distinguished, so CH2 groups next to stereocentres may show
    more signals than predicted; the UI words its comparison accordingly."""
    mol = mol_from_any(text)
    molH = Chem.AddHs(mol)
    ranks = list(Chem.CanonicalRankAtoms(molH, breakTies=False))
    carbons = {ranks[a.GetIdx()] for a in molH.GetAtoms() if a.GetSymbol() == "C"}
    groups: dict[int, dict[str, Any]] = {}
    for a in molH.GetAtoms():
        if a.GetSymbol() != "H":
            continue
        heavy = a.GetNeighbors()[0]
        g = groups.setdefault(ranks[a.GetIdx()], {"count": 0, "on": heavy.GetSymbol(),
                                                   "aromatic": heavy.GetIsAromatic()})
        g["count"] += 1
    env = sorted(groups.values(), key=lambda g: -g["count"])
    exchangeable = sum(g["count"] for g in env if g["on"] in ("O", "N", "S"))
    return {
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "total_h": sum(g["count"] for g in env),
        "exchangeable_h": exchangeable,
        "h_environments": [{"count": g["count"], "on": g["on"], "aromatic": g["aromatic"]} for g in env],
        "unique_carbons": len(carbons),
        "total_carbons": sum(1 for a in mol.GetAtoms() if a.GetSymbol() == "C"),
    }


# monoisotopic masses (IUPAC/NIST)
_E = 0.000548580
_ADDUCTS = [  # name, sign, atoms added (+) or removed (-), charge
    ("[M + H]+", {"H": 1}, 1), ("[M + Na]+", {"Na": 1}, 1), ("[M + K]+", {"K": 1}, 1),
    ("[M + NH4]+", {"N": 1, "H": 4}, 1), ("[M]+", {}, 1), ("[M − H]−", {"H": -1}, -1),
]
_MONO = {"H": 1.00782503207, "C": 12.0, "N": 14.0030740048, "O": 15.99491461956, "Na": 22.9897692809,
         "K": 38.96370668, "F": 18.99840322, "Cl": 34.96885268, "Br": 78.9183371, "I": 126.904473,
         "S": 31.97207100, "P": 30.97376163, "Si": 27.9769265325, "B": 11.0093054, "Se": 79.9165213}


def _counts(mol: Chem.Mol) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in Chem.AddHs(mol).GetAtoms():
        out[a.GetSymbol()] = out.get(a.GetSymbol(), 0) + 1
    return out


def _hill(c: dict[str, int]) -> str:
    keys = sorted(c, key=lambda k: (k != "C", k != "H" if "C" in c else False, k))
    return "".join(f"{k}{c[k] if c[k] != 1 else ''}" for k in keys if c[k] > 0)


def hrms_ions(text: str) -> dict[str, Any]:
    mol = mol_from_any(text)
    base = _counts(mol)
    missing = [k for k in base if k not in _MONO]
    if missing:
        raise ChemError(f"No monoisotopic mass for {', '.join(missing)}")
    neutral = sum(_MONO[k] * n for k, n in base.items()) - Chem.GetFormalCharge(mol) * _E
    ions = []
    for name, delta, z in _ADDUCTS:
        c = dict(base)
        for k, n in delta.items():
            c[k] = c.get(k, 0) + n
        if any(v < 0 for v in c.values()):
            continue
        m = sum(_MONO[k] * n for k, n in c.items()) - z * _E
        ions.append({"adduct": name, "formula": _hill(c), "charge": z, "mz": round(m / abs(z), 4)})
    return {"formula": _hill(base), "monoisotopic": round(neutral, 4), "ions": ions}
