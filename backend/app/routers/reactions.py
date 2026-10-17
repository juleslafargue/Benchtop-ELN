"""Cross-experiment view of every reaction: conditions, scale, outcome. Filterable, exportable."""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import select

from .. import chem
from ..models import Entry, EntryKind, Experiment
from ..security import DB, CurrentUser
from ..services import user_brief

router = APIRouter(prefix="/api", tags=["reactions"])


def _collect(session) -> list[dict[str, Any]]:
    exps = {e.id: e for e in session.exec(select(Experiment).where(Experiment.is_template == False))}  # noqa: E712
    entries = session.exec(select(Entry).where(Entry.deleted == False).order_by(Entry.experiment_id, Entry.position))  # noqa: E712
    by_exp: dict[int, list[Entry]] = {}
    for en in entries:
        if en.experiment_id in exps:
            by_exp.setdefault(en.experiment_id, []).append(en)
    out = []
    for exp_id, ens in by_exp.items():
        exp = exps[exp_id]
        purif = next((e.content for e in ens if e.kind == EntryKind.purification), None) or {}
        chars = [it.get("technique") for e in ens if e.kind == EntryKind.characterization
                 for it in (e.content or {}).get("items", []) if it.get("data")]
        for en in ens:
            if en.kind != EntryKind.reaction:
                continue
            c = en.content or {}
            rows = c.get("rows") or []
            if not rows and not c.get("reaction_smiles"):
                continue
            products = [r for r in rows if r.get("role") == "product"]
            lim = next((r for r in rows if r.get("limiting")), None)
            best = max((p for p in products if p.get("yield_pct") is not None), key=lambda p: p["yield_pct"], default=None)
            cond = c.get("conditions") or {}
            out.append({
                "experiment_id": exp.id, "eid": exp.eid, "title": exp.title, "status": exp.status.value,
                "owner": user_brief(session, exp.owner_id), "tags": exp.tags or [],
                "date": exp.created_at.isoformat() + "Z", "entry_title": en.title,
                "reaction_smiles": c.get("reaction_smiles", ""),
                "temperature_c": cond.get("temperature_c"), "temperature_label": cond.get("temperature_label"),
                "time_h": cond.get("time_h"), "atmosphere": cond.get("atmosphere"),
                "concentration_m": (c.get("totals") or {}).get("concentration_m"),
                "limiting": lim.get("name") or lim.get("formula") if lim else None,
                "scale_mmol": (c.get("totals") or {}).get("limiting_mmol"),
                "reagents": [r.get("name") or r.get("formula") for r in rows if r.get("role") in ("reagent", "catalyst")],
                "solvents": [r.get("name") or r.get("formula") for r in rows if r.get("role") == "solvent"],
                "product": (best or (products[0] if products else {})).get("name") or (best or {}).get("formula"),
                "yield_pct": best["yield_pct"] if best else None,
                "purification": purif.get("method") if purif.get("isolated_mg") is not None or purif.get("eluent") else None,
                "characterised": chars,
                "smiles_all": [r.get("smiles") for r in rows if r.get("smiles")],
            })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def _filter(rows, q, min_yield, max_yield, min_temp, max_temp, atmosphere, substructure, status):
    match = None
    if substructure:
        try:
            match = chem.substructure_matcher(substructure)
        except chem.ChemError as exc:
            raise HTTPException(422, str(exc))
    ql = q.lower().strip()
    res = []
    for r in rows:
        if ql:
            hay = " ".join(str(x) for x in (r["title"], r["eid"], r["limiting"], r["product"], " ".join(r["reagents"]),
                                              " ".join(r["solvents"]), " ".join(r["tags"]), r["temperature_label"])).lower()
            if ql not in hay:
                continue
        y, t = r["yield_pct"], r["temperature_c"]
        if min_yield is not None and (y is None or y < min_yield):
            continue
        if max_yield is not None and (y is None or y > max_yield):
            continue
        if min_temp is not None and (t is None or t < min_temp):
            continue
        if max_temp is not None and (t is None or t > max_temp):
            continue
        if atmosphere and (r["atmosphere"] or "").lower() != atmosphere.lower():
            continue
        if status and r["status"] != status:
            continue
        if match and not any(match(s) for s in r["smiles_all"]):
            continue
        res.append(r)
    return res


@router.get("/reactions")
def list_reactions(_: CurrentUser, session: DB, q: str = "", min_yield: float | None = None,
                   max_yield: float | None = None, min_temp: float | None = None, max_temp: float | None = None,
                   atmosphere: str = "", substructure: str = "", status: str = "", format: str = "json"):
    rows = _filter(_collect(session), q, min_yield, max_yield, min_temp, max_temp, atmosphere, substructure, status)
    if format != "csv":
        return rows
    buf = io.StringIO()
    cols = ["eid", "title", "status", "date", "limiting", "scale_mmol", "reagents", "solvents", "temperature_c",
            "temperature_label", "time_h", "atmosphere", "concentration_m", "product", "yield_pct", "purification",
            "characterised", "reaction_smiles"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow(["; ".join(r[c]) if isinstance(r[c], list) else ("" if r[c] is None else r[c]) for c in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="reactions.csv"'})
