"""NMR chemical-shift prediction from NMRShiftDB2.

Method: each atom's surroundings are described sphere by sphere (the HOSE-code idea of
Bremser 1978, implemented as RDKit environment hashes): sphere 0 is the atom itself, sphere 1
adds its neighbours, and so on up to sphere 4. For every assigned atom in NMRShiftDB2 the observed shift is added to running
statistics for each of its codes. To predict, we look up the most specific code that has data
(4 spheres, then 3, 2, 1) and report the mean, the spread and how many examples it rests on.

13C shifts are keyed by the carbon's environment; 1H shifts by the environment of the heavy
atom carrying the hydrogen.

Data: NMRShiftDB2 (https://nmrshiftdb.nmr.uni-koeln.de), nmrshiftdb2 Data License. The table
built from it is a derived database under the same licence (see data/NMRSHIFTDB2_LICENSE.md).
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from rdkit import Chem, RDLogger

from .config import settings

RDLogger.DisableLog("rdApp.*")

MAX_R = 4
NUC = {"13C": 13, "1H": 1}
RANGE = {13: (-20.0, 240.0), 1: (-3.0, 17.0)}
BUNDLED = Path(__file__).parent / "data" / "nmr_shifts.sqlite"
_BOND = {Chem.BondType.SINGLE: "-", Chem.BondType.DOUBLE: "=", Chem.BondType.TRIPLE: "#", Chem.BondType.AROMATIC: ":"}


def user_table() -> Path:
    return settings.data_dir / "nmr_shifts.sqlite"


def table_path() -> Optional[Path]:
    """The table built on this machine from the latest download wins over the one shipped with Benchtop."""
    for p in (user_table(), BUNDLED):
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


# ---------------------------------------------------------------- environment codes

def _h64(data: bytes) -> int:
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big", signed=True)


_BOND = {Chem.BondType.SINGLE: 1, Chem.BondType.DOUBLE: 2, Chem.BondType.TRIPLE: 3, Chem.BondType.AROMATIC: 4}


def environments(mol: Chem.Mol, max_r: int = MAX_R) -> list[list[int]]:
    """For each atom of a hydrogen-suppressed molecule: [env_r0, env_r1, ..., env_rmax].
    Each radius folds in the previous radius of every neighbour (with the bond order), so a code
    captures exact connectivity out to that many bonds. Stable across Python and RDKit versions.
    (On held-out NMRShiftDB2 molecules this beat a looser sphere-list code: 13C median error
    1.24 vs 1.35 ppm.)"""
    n = mol.GetNumAtoms()
    cur = []
    for a in mol.GetAtoms():
        inv = (a.GetAtomicNum(), int(a.GetIsAromatic()), a.GetDegree(), a.GetTotalNumHs(), a.GetFormalCharge() + 8,
               int(a.IsInRing()), int(a.IsInRingSize(3) or a.IsInRingSize(4)))
        cur.append(_h64(bytes(inv)))
    out = [[c] for c in cur]
    nbrs = [[(_BOND.get(b.GetBondType(), 1), b.GetOtherAtomIdx(a.GetIdx())) for b in a.GetBonds()] for a in mol.GetAtoms()]
    for _ in range(max_r):
        nxt = []
        for i in range(n):
            parts = sorted((bo, cur[j]) for bo, j in nbrs[i])
            payload = cur[i].to_bytes(8, "big", signed=True) + b"".join(bytes([bo]) + h.to_bytes(8, "big", signed=True) for bo, h in parts)
            nxt.append(_h64(payload))
        cur = nxt
        for i in range(n):
            out[i].append(cur[i])
    return out


def heavy_envs(mol: Chem.Mol) -> Optional[dict[int, list[int]]]:
    """Environment codes keyed by the atom indices of `mol` (which may contain explicit hydrogens)."""
    for a in mol.GetAtoms():
        a.SetIntProp("_orig", a.GetIdx())
    try:
        heavy = Chem.RemoveHs(mol)
    except Exception:  # noqa: BLE001 - a few records have odd valences
        return None
    envs = environments(heavy)
    return {a.GetIntProp("_orig"): envs[a.GetIdx()] for a in heavy.GetAtoms() if a.HasProp("_orig")}


# ---------------------------------------------------------------- building the table

def read_sd(path: Path) -> Iterable[Chem.Mol]:
    """NMRShiftDB2 molecules with their real hydrogen counts.

    The export comes from CDK with hydrogens stripped and each atom's valence field set, which
    RDKit would honour literally: every carbon would read as a radical with no hydrogens, so CH3,
    CH2, CH and C would all look alike. We drop those fields and let RDKit work out the hydrogens."""
    for mol in Chem.SDMolSupplier(str(path), removeHs=False, sanitize=False, strictParsing=False):
        if mol is None:
            continue
        for a in mol.GetAtoms():
            a.SetNoImplicit(False)
            a.SetNumRadicalElectrons(0)
        try:
            Chem.SanitizeMol(mol)
        except Exception:  # noqa: BLE001 - a few records have impossible valences
            continue
        yield mol


def _parse_peaks(value: str) -> list[tuple[float, int]]:
    out = []
    for item in str(value).split("|"):
        bits = item.strip().split(";")
        if len(bits) < 3:
            continue
        try:
            out.append((float(bits[0]), int(bits[-1])))
        except ValueError:
            continue
    return out


def iter_observations(mol: Chem.Mol) -> Iterable[tuple[int, int, float]]:
    """(nucleus, heavy-atom index, shift) for every assigned peak in one NMRShiftDB2 record.
    Several spectra of one molecule are averaged per atom so a well-studied compound doesn't dominate."""
    acc: dict[tuple[int, int], list[float]] = {}
    n_atoms = mol.GetNumAtoms()
    for key in mol.GetPropNames():
        if key.startswith("Spectrum 13C"):
            nuc = 13
        elif key.startswith("Spectrum 1H"):
            nuc = 1
        else:
            continue
        lo, hi = RANGE[nuc]
        for shift, i in _parse_peaks(mol.GetProp(key)):
            if not (lo <= shift <= hi) or not (0 <= i < n_atoms):
                continue
            a = mol.GetAtomWithIdx(i)
            if nuc == 13 and a.GetAtomicNum() != 6:
                continue
            if nuc == 1:
                if a.GetAtomicNum() == 1:  # assigned to an explicit H: key by the atom it sits on
                    nbrs = a.GetNeighbors()
                    if not nbrs:
                        continue
                    a = nbrs[0]
                if a.GetTotalNumHs(includeNeighbors=True) == 0:
                    continue
            acc.setdefault((nuc, a.GetIdx()), []).append(shift)
    for (nuc, i), vals in acc.items():
        yield nuc, i, sum(vals) / len(vals)


def build(sd_files: list[Path], out: Path, source: str, *, progress=None, holdout: float = 0.0) -> dict[str, Any]:
    """Compile NMRShiftDB2 SD files into a shift table. `holdout` keeps a fraction of molecules out
    (by a stable hash) for an honest accuracy estimate."""
    stats: dict[tuple[int, int, int], list[float]] = {}  # (nuc, r, key) -> [n, sum, sumsq, min, max]
    held: list[tuple[Chem.Mol, list[tuple[int, int, float]]]] = []
    sha = hashlib.sha256()
    n_mol = n_obs = 0
    nuc_obs = {1: 0, 13: 0}
    for path in sd_files:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                sha.update(chunk)
        for mol in read_sd(path):
            obs = list(iter_observations(mol))
            if not obs:
                continue
            n_mol += 1
            if progress and n_mol % 2000 == 0:
                progress(n_mol)
            if holdout and (int(hashlib.md5(Chem.MolToSmiles(mol).encode()).hexdigest(), 16) % 1000) < holdout * 1000:
                held.append((mol, obs))
                continue
            envs = heavy_envs(mol)
            if envs is None:
                continue
            for nuc, i, shift in obs:
                codes = envs.get(i)
                if codes is None:
                    continue
                for r in range(1, MAX_R + 1):
                    s = stats.get((nuc, r, codes[r]))
                    if s is None:
                        stats[(nuc, r, codes[r])] = [1, shift, shift * shift, shift, shift]
                    else:
                        s[0] += 1; s[1] += shift; s[2] += shift * shift
                        s[3] = min(s[3], shift); s[4] = max(s[4], shift)
                n_obs += 1
                nuc_obs[nuc] += 1
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE stats (nuc INTEGER, r INTEGER, key INTEGER, n INTEGER, s REAL, ss REAL, mn REAL, mx REAL, PRIMARY KEY (nuc, r, key)) WITHOUT ROWID")
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    # single observations at the most specific radii add size without much information
    rows = ((nuc, r, k, *v) for (nuc, r, k), v in stats.items() if not (r >= 3 and v[0] < 2))
    con.executemany("INSERT INTO stats VALUES (?,?,?,?,?,?,?,?)", rows)
    meta = {"source": source, "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "molecules": str(n_mol - len(held)), "assignments_13C": str(nuc_obs[13]), "assignments_1H": str(nuc_obs[1]),
            "input_sha256": sha.hexdigest(), "method": f"HOSE-style sphere codes, radius 1-{MAX_R}", "format": "1"}
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    con.execute("VACUUM")
    con.close()
    tmp.replace(out)
    reset_cache()
    result = {**meta, "rows": len(stats)}
    if held:
        result["holdout"] = evaluate(held, out)
    return result


def evaluate(held, table: Path) -> dict[str, Any]:
    """Mean absolute error on molecules the table never saw."""
    p = Predictor(table)
    err: dict[int, list[float]] = {1: [], 13: []}
    for mol, obs in held:
        envs = heavy_envs(mol) or {}
        for nuc, i, shift in obs:
            pr = p.lookup(nuc, envs[i]) if i in envs else None
            if pr:
                err[nuc].append(abs(pr["shift"] - shift))
    out = {}
    for nuc, e in err.items():
        if e:
            e.sort()
            out["13C" if nuc == 13 else "1H"] = {"mae": round(sum(e) / len(e), 2), "median": round(e[len(e) // 2], 2),
                                                 "within_2ppm" if nuc == 13 else "within_0.3ppm": round(sum(x <= (2 if nuc == 13 else 0.3) for x in e) / len(e), 3),
                                                 "n": len(e), "molecules": len(held)}
    return out


# ---------------------------------------------------------------- predicting

class Predictor:
    MIN_N = {4: 2, 3: 2, 2: 2, 1: 1}

    def __init__(self, path: Path):
        self.path = path
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.meta = dict(self.con.execute("SELECT k, v FROM meta").fetchall())
        self.lock = threading.Lock()

    def lookup(self, nuc: int, codes: list[int]) -> Optional[dict[str, Any]]:
        with self.lock:
            for r in range(MAX_R, 0, -1):
                row = self.con.execute("SELECT n, s, ss, mn, mx FROM stats WHERE nuc=? AND r=? AND key=?", (nuc, r, codes[r])).fetchone()
                if row and row[0] >= self.MIN_N[r]:
                    n, s, ss, mn, mx = row
                    mean = s / n
                    sd = math.sqrt(max(0.0, ss / n - mean * mean))
                    return {"shift": round(mean, 2 if nuc == 13 else 2), "sd": round(sd, 2), "n": n, "radius": r,
                            "min": round(mn, 2), "max": round(mx, 2)}
        return None

    def has(self, nuc: int) -> bool:
        with self.lock:
            return self.con.execute("SELECT 1 FROM stats WHERE nuc=? LIMIT 1", (nuc,)).fetchone() is not None


_pred: Optional[Predictor] = None
_pred_path: Optional[Path] = None


def reset_cache() -> None:
    global _pred, _pred_path
    _pred, _pred_path = None, None


def predictor() -> Optional[Predictor]:
    global _pred, _pred_path
    p = table_path()
    if p is None:
        return None
    if _pred is None or _pred_path != p:
        _pred, _pred_path = Predictor(p), p
    return _pred


class NotReady(Exception):
    """No shift table (or no data for this nucleus) yet."""


def confidence(r: dict[str, Any], nuc: int) -> str:
    if r["radius"] >= 3 and r["n"] >= 3:
        return "high"
    if r["radius"] >= 2:
        return "medium"
    return "low"


def predict(smiles: str, nucleus: str = "1H") -> dict[str, Any]:
    """Predicted shifts for each symmetry-distinct group of carbons (13C) or of H-bearing atoms (1H).
    Atom indices refer to the returned SMILES (the main fragment), so the depiction can number them."""
    if nucleus not in NUC:
        raise ValueError("nucleus must be 1H or 13C")
    nuc = NUC[nucleus]
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        raise ValueError("Couldn't read that structure")
    frags = Chem.GetMolFrags(mol, asMols=True)
    main = max(frags, key=lambda f: f.GetNumHeavyAtoms())
    smi = Chem.MolToSmiles(main)
    mol = Chem.MolFromSmiles(smi)  # indices now match a fresh parse of `smi`
    p = predictor()
    if p is None:
        raise NotReady("Prediction data hasn't been downloaded yet")
    if not p.has(nuc):
        raise NotReady(f"The prediction table has no {nucleus} data yet; it will after the first download of the full NMRShiftDB2 export")
    ranks = list(Chem.CanonicalRankAtoms(mol, breakTies=False))
    envs = environments(mol)
    groups: dict[int, dict[str, Any]] = {}
    for a in mol.GetAtoms():
        i, nh = a.GetIdx(), a.GetTotalNumHs()
        if (nuc == 13 and a.GetAtomicNum() != 6) or (nuc == 1 and nh == 0):
            continue
        g = groups.get(ranks[i])
        if g is None:
            r = p.lookup(nuc, envs[i])
            g = groups[ranks[i]] = {
                "atoms": [], "nH": 0, "element": a.GetSymbol(), "aromatic": a.GetIsAromatic(),
                "exchangeable": nuc == 1 and a.GetSymbol() in ("O", "N", "S"),
                "shift": r["shift"] if r else None, "confidence": confidence(r, nuc) if r else "none",
                "vicinal_h": sum(n.GetTotalNumHs() for n in a.GetNeighbors() if n.GetSymbol() == "C") if nuc == 1 else None,
                **({"sd": r["sd"], "n": r["n"], "sphere": r["radius"], "lo": r["min"], "hi": r["max"]} if r else {}),
            }
        g["atoms"].append(i)
        g["nH"] += nh if nuc == 1 else 0
    out = sorted(groups.values(), key=lambda g: -(g["shift"] if g["shift"] is not None else -1e9))
    return {"nucleus": nucleus, "smiles": smi, "groups": out,
            "covered": sum(1 for g in out if g["shift"] is not None), "total": len(out),
            "source": {"source": p.meta.get("source"), "built_at": p.meta.get("built_at"), "molecules": p.meta.get("molecules"),
                       "assigned_atoms": p.meta.get(f"assignments_{nucleus}"), "bundled": p.path == BUNDLED}}


def status() -> dict[str, Any]:
    from . import nmrdata
    pr = nmrdata.progress()
    stage = pr.get("stage")
    msg = ""
    if stage == "downloading":
        msg = f"Downloading NMRShiftDB2: {pr.get('done_mb', 0)} MB" + (f" of {pr['total_mb']} MB" if pr.get("total_mb") else "")
    elif stage == "building":
        msg = f"Building the prediction table: {pr.get('molecules', 0):,} structures read"
    elif stage == "checking":
        msg = "Checking NMRShiftDB2 for a newer export"
    p = predictor()
    out = {"available": p is not None, "updating": bool(pr.get("running")), "progress": msg,
           "last_error": pr.get("last_error") or "", "refresh_days": 30, "auto_update": settings.nmr_auto_update,
           "last_check": pr.get("last_check"), "last_update": pr.get("last_update")}
    if p is not None:
        out["meta"] = {**p.meta, "table": "downloaded" if p.path != BUNDLED else "bundled with Benchtop"}
        out["has_13C"], out["has_1H"] = p.has(13), p.has(1)
        try:
            built = datetime.fromisoformat(p.meta["built_at"].replace("Z", "+00:00"))
            out["age_days"] = round((datetime.now(timezone.utc) - built).total_seconds() / 86400, 1)
        except (KeyError, ValueError):
            out["age_days"] = None
    return out
