"""Simulated NMR spectra for demo compounds, built from Benchtop's own shift prediction.

Each proton group becomes a first-order multiplet (n+1 lines, binomial intensities) with a
Lorentzian line shape, plus the deuterated solvent's residual peak, water, TMS, a little noise
and, optionally, a residual lab solvent. Good enough to look and behave like a real spectrum in
the viewer (auto-analysis, integration, assignment); not a substitute for a measured one.
"""
from __future__ import annotations

import random
from math import comb

import numpy as np
from rdkit import Chem

from . import nmrpredict

SOLVENTS = {
    "CDCl3": {"1H": [(7.26, 0.10), (1.56, 0.25), (0.00, 0.20)], "13C": [(77.16, 3)], "label": "CDCl3"},
    "DMSO-d6": {"1H": [(2.50, 0.22), (3.33, 0.6)], "13C": [(39.52, 7)], "label": "DMSO-d6"},
}
# residual lab solvents, as (shift, nH, multiplicity, J) per solvent (Fulmer et al. 2010)
IMPURITIES = {
    ("EtOAc", "CDCl3"): [(4.12, 2, 3, 7.1), (2.05, 3, 0, 0), (1.26, 3, 2, 7.1)],
    ("EtOAc", "DMSO-d6"): [(4.03, 2, 3, 7.1), (1.99, 3, 0, 0), (1.17, 3, 2, 7.1)],
    ("hexane", "CDCl3"): [(1.26, 8, 0, 0), (0.88, 6, 2, 7.0)],
    ("DCM", "CDCl3"): [(5.30, 2, 0, 0)],
}
LETTER = {1: "s", 2: "d", 3: "t", 4: "q", 5: "p", 7: "hept"}


def _lines(center: float, n_h: float, n_lines: int, j_hz: float, mhz: float) -> list[tuple[float, float]]:
    n = n_lines - 1
    return [(center + (k - n / 2) * j_hz / mhz, n_h * comb(n, k) / 2 ** n) for k in range(n + 1)]


def _jcamp(xs: np.ndarray, ys: np.ndarray, title: str, nucleus: str, mhz: float, solvent: str) -> str:
    ints = np.round(ys / np.abs(ys).max() * 1_000_000).astype(int)
    first, last, n = float(xs[0]), float(xs[-1]), len(xs)
    out = [f"##TITLE={title}", "##JCAMP-DX=5.01", "##DATA TYPE=NMR SPECTRUM", "##DATA CLASS=XYDATA",
           f"##.OBSERVE NUCLEUS=^{nucleus}", f"##.OBSERVE FREQUENCY={mhz}", f"##.SOLVENT NAME={solvent}",
           "##XUNITS=PPM", "##YUNITS=ARBITRARY UNITS", "##XFACTOR=1", "##YFACTOR=1",
           f"##FIRSTX={first}", f"##LASTX={last}", f"##DELTAX={(last - first) / (n - 1):.10f}", f"##NPOINTS={n}",
           f"##FIRSTY={ints[0]}", "##XYDATA=(X++(Y..Y))"]
    for i in range(0, n, 10):
        out.append(f"{xs[i]:.6f} " + " ".join(str(v) for v in ints[i:i + 10]))
    out.append("##END=")
    return "\n".join(out) + "\n"


def _render(lines, first, last, npts, width_ppm, noise, seed):
    xs = np.linspace(first, last, npts)
    ys = np.zeros(npts)
    for c, a, w in lines:
        g = w or width_ppm
        m = np.abs(xs - c) < 60 * g
        # Lorentzian scaled so its area is proportional to `a` (a broad NH keeps its 1H integral)
        ys[m] += a * width_ppm * g / ((xs[m] - c) ** 2 + g * g)
    rng = np.random.default_rng(seed)
    ys += rng.normal(0, noise * ys.max(), npts)  # zero-mean noise, so integrals over wide regions stay honest
    return xs, ys


def proton(smiles: str, title: str, solvent: str = "CDCl3", mhz: float = 400.0, impurity: tuple[str, float] | None = None,
           seed: int = 1, exch: float | None = None) -> tuple[str, str]:
    """(JCAMP text, characterisation line like 'δ 7.52 (d, J = 8.4 Hz, 2H), …')."""
    pred = nmrpredict.predict(smiles, "1H")
    mol = Chem.MolFromSmiles(pred["smiles"])
    rnd = random.Random(seed)
    lines, signals = [], []
    for g in pred["groups"]:
        if g["shift"] is None:
            continue
        shift = round(g["shift"] + rnd.uniform(-0.02, 0.02), 3)
        if g["exchangeable"] and exch is not None:
            shift = exch  # where the OH/NH actually shows up in this solvent (concentration-dependent)
        if g["exchangeable"]:
            if solvent == "CDCl3" and g["shift"] > 9:  # COOH and the like: very broad in CDCl3
                lines.append((shift, g["nH"], 18 / mhz))
            else:
                lines.append((shift, g["nH"], 4 / mhz))
            signals.append((shift, "br s", None, g["nH"]))
            continue
        n_lines = min((g.get("vicinal_h") or 0) + 1, 7)
        def near_n(a, depth):  # pyridine-type H: alpha (and beta) to a ring N couple at ~5 Hz
            return any((nb.GetSymbol() == "N" and nb.GetIsAromatic()) or (depth > 1 and nb.GetIsAromatic() and nb.GetTotalNumHs() and near_n(nb, depth - 1))
                       for nb in a.GetNeighbors())
        alpha_n = any(near_n(mol.GetAtomWithIdx(i), 2) for i in g["atoms"])
        j = (5.2 if alpha_n else 8.3) if g["aromatic"] else 7.1
        for c, a in _lines(shift, g["nH"], n_lines, j, mhz):
            lines.append((c, a, None))
        letter = LETTER.get(n_lines, "m")
        signals.append((shift, letter, j if letter in ("d", "t", "q", "p", "hept") else None, g["nH"]))
    for c, a in SOLVENTS[solvent]["1H"]:
        lines.append((c, a, None))
    if impurity:
        name, mol_frac = impurity
        for c, nh, mult, j in IMPURITIES.get((name, solvent), []):
            for x, a in _lines(c, nh * mol_frac, mult + 1, j, mhz):
                lines.append((x, a, None))
    xs, ys = _render(lines, 12.0 if any(s[0] > 9.5 for s in signals) else 10.0, -0.5, 65536, 0.55 / mhz, 0.0008, seed)
    text = "δ " + ", ".join(
        f"{s:.2f} ({m}{f', J = {j:.1f} Hz' if j else ''}, {round(n)}H)" for s, m, j, n in sorted(signals, key=lambda t: -t[0]))
    return _jcamp(xs, ys, title, "1H", mhz, SOLVENTS[solvent]["label"]), text


def carbon(smiles: str, title: str, solvent: str = "CDCl3", mhz: float = 101.0, seed: int = 2) -> tuple[str, str]:
    pred = nmrpredict.predict(smiles, "13C")
    mol = Chem.MolFromSmiles(pred["smiles"])
    rnd = random.Random(seed)
    lines, shifts = [], []
    for g in pred["groups"]:
        if g["shift"] is None:
            continue
        shift = round(g["shift"] + rnd.uniform(-0.15, 0.15), 1)
        nh = mol.GetAtomWithIdx(g["atoms"][0]).GetTotalNumHs()
        lines.append((shift, len(g["atoms"]) * (0.35 if nh == 0 else 1.0), None))
        shifts.append(shift)
    c0, n = SOLVENTS[solvent]["13C"][0]
    j = 32.0 if solvent == "CDCl3" else 21.0
    for x, a in _lines(c0, 3.0, n, j, mhz):
        lines.append((x, a, None))
    xs, ys = _render(lines, 220.0, -5.0, 65536, 1.2 / mhz, 0.004, seed)
    text = "δ " + ", ".join(f"{s:.1f}" for s in sorted(shifts, reverse=True))
    return _jcamp(xs, ys, title, "13C", mhz, SOLVENTS[solvent]["label"]), text
