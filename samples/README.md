# Sample spectra (synthetic)

Simulated Bruker experiment folders (zipped) for trying drag-and-drop import, auto-analysis, impurity
detection and overlays. They are generated, not measured: Lorentzian lines with added noise, based on
typical literature shifts for 4-methoxybiphenyl and 4-bromoanisole in CDCl₃.

| File | What it shows |
|---|---|
| `JL-042_1H.zip` | Pure product, ¹H 400 MHz. The axis is deliberately mis-referenced by +0.03 ppm, so the referencing check appears. |
| `JL-042_1H_EtOAc.zip` | Product with 5 mol% ethyl acetate and 2 mol% hexane: residual-solvent detection and purity estimate. |
| `JL-042_1H_crude.zip` | Crude mixture with ~15% unreacted 4-bromoanisole and EtOAc. |
| `4-bromoanisole_1H.zip` | Starting material, for overlaying on the crude. |
| `JL-042_13C.zip` | ¹³C 101 MHz with the CDCl₃ triplet at 77.16: peak list and carbon count. |

Drag them onto the demo experiment "Suzuki coupling of 4-bromoanisole with phenylboronic acid", whose product
formula (C₁₃H₁₂O) is used to scale integrals and check the structure. To compare, open the crude spectrum and
click **Compare…**, then pick the starting material.
