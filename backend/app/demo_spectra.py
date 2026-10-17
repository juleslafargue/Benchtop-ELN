"""Synthetic spectra for the demo data, written as JCAMP-DX (AFFN), the open exchange
format most NMR/IR/MS software can export."""
from __future__ import annotations

from math import comb

MHZ = 400.0


def _multiplet(center_ppm: float, n_h: float, mult: str, j_hz: float = 0.0) -> list[tuple[float, float]]:
    """First-order lines (ppm, intensity) for s/d/t/q multiplets."""
    n = {"s": 0, "d": 1, "t": 2, "q": 3}[mult]
    lines = []
    for k in range(n + 1):
        offset_hz = (k - n / 2) * j_hz
        lines.append((center_ppm + offset_hz / MHZ, n_h * comb(n, k) / 2 ** n))
    return lines


# 4-methoxybiphenyl in CDCl3 (first-order approximation)
METHOXYBIPHENYL = [
    *_multiplet(7.548, 2, "d", 7.6), *_multiplet(7.524, 2, "d", 8.8), *_multiplet(7.416, 2, "t", 7.6),
    *_multiplet(7.302, 1, "t", 7.4), *_multiplet(6.978, 2, "d", 8.8), *_multiplet(3.858, 3, "s"),
    (7.260, 0.12), (1.56, 0.25), (0.0, 0.3),  # CHCl3, H2O, TMS
]


def nmr_jcamp(lines, title: str, first=10.0, last=-0.5, npts=32768, width_hz=0.9) -> str:
    gamma = width_hz / 2 / MHZ  # Lorentzian half-width in ppm
    step = (last - first) / (npts - 1)
    xs = [first + i * step for i in range(npts)]
    ys = []
    for x in xs:
        y = 0.0
        for c, a in lines:
            d = x - c
            if abs(d) < 0.2:
                y += a * gamma * gamma / (d * d + gamma * gamma)
        ys.append(y)
    peak = max(ys)
    ints = [int(round(y / peak * 1_000_000)) for y in ys]
    out = [
        f"##TITLE={title}", "##JCAMP-DX=5.01", "##DATA TYPE=NMR SPECTRUM", "##DATA CLASS=XYDATA",
        "##.OBSERVE NUCLEUS=^1H", f"##.OBSERVE FREQUENCY={MHZ}", "##.SOLVENT NAME=CDCl3",
        "##XUNITS=PPM", "##YUNITS=ARBITRARY UNITS", "##XFACTOR=1", "##YFACTOR=1",
        f"##FIRSTX={first}", f"##LASTX={last}", f"##DELTAX={step:.10f}", f"##NPOINTS={npts}",
        f"##FIRSTY={ints[0]}", "##XYDATA=(X++(Y..Y))",
    ]
    for i in range(0, npts, 10):
        out.append(f"{xs[i]:.6f} " + " ".join(str(v) for v in ints[i:i + 10]))
    out.append("##END=")
    return "\n".join(out) + "\n"
