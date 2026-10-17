"""Full-text and structure search across experiments and the materials registry."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, or_, select

from .. import audit, chem
from ..models import AuditEvent, Entry, EntryKind, Experiment, Material
from ..security import DB, Admin, CurrentUser
from ..services import experiment_summary
from .experiments import audit_out

router = APIRouter(prefix="/api", tags=["search & audit"])

_TAG = re.compile(r"<[^>]+>")


def _snippet(text: str, q: str, width: int = 70) -> str:
    plain = _TAG.sub(" ", text)
    i = plain.lower().find(q.lower())
    if i < 0:
        return ""
    s = max(0, i - width)
    return ("…" if s else "") + plain[s:i + len(q) + width].strip() + "…"


@router.get("/search")
def search(_: CurrentUser, session: DB, q: str = "", mode: str = "text", threshold: float = 0.5,
           limit: int = 100):
    """mode = text | substructure | similarity"""
    q = q.strip()
    if not q:
        return {"experiments": [], "materials": []}

    if mode == "text":
        like = f"%{q}%"
        hits: dict[int, dict[str, Any]] = {}
        for e in session.exec(select(Experiment).where(or_(
                col(Experiment.title).ilike(like), col(Experiment.description).ilike(like))).limit(limit)):
            hits[e.id] = {**experiment_summary(session, e), "match": "title/description"}
        # entry content is JSON; scan in Python so it works the same on SQLite and Postgres
        for en in session.exec(select(Entry).where(Entry.deleted == False)):  # noqa: E712
            if en.experiment_id in hits:
                continue
            blob = audit.canonical(en.content) + " " + en.title
            if q.lower() in blob.lower():
                exp = session.get(Experiment, en.experiment_id)
                text = (en.content or {}).get("html", "") if en.kind == EntryKind.text else blob
                hits[exp.id] = {**experiment_summary(session, exp), "match": en.title,
                                "snippet": _snippet(text, q)}
            if len(hits) >= limit:
                break
        mats = session.exec(select(Material).where(or_(
            col(Material.name).ilike(like), col(Material.cas).ilike(like),
            col(Material.synonyms).ilike(like), col(Material.formula).ilike(like))).limit(limit)).all()
        return {"experiments": list(hits.values()),
                "materials": [{"id": m.id, "mid": m.mid, "name": m.name, "smiles": m.smiles,
                               "formula": m.formula, "mw": m.mw, "cas": m.cas} for m in mats]}

    try:
        if mode == "substructure":
            fn = chem.substructure_matcher(q)
            scorer = lambda s: 1.0 if fn(s) else 0.0  # noqa: E731
            cutoff = 0.5
        elif mode == "similarity":
            scorer = chem.similarity_scorer(q)
            cutoff = threshold
        else:
            raise HTTPException(400, "mode must be text, substructure or similarity")
    except chem.ChemError as exc:
        raise HTTPException(422, str(exc))

    mats = []
    for m in session.exec(select(Material).where(Material.smiles != "")):
        s = scorer(m.smiles)
        if s >= cutoff:
            mats.append({"id": m.id, "mid": m.mid, "name": m.name, "smiles": m.smiles,
                         "formula": m.formula, "mw": m.mw, "cas": m.cas, "score": round(s, 3)})
    exps: dict[int, dict[str, Any]] = {}
    for en in session.exec(select(Entry).where(Entry.kind == EntryKind.reaction, Entry.deleted == False)):  # noqa: E712
        best, best_row = 0.0, None
        for r in (en.content or {}).get("rows", []):
            s = scorer(r.get("smiles", ""))
            if s > best:
                best, best_row = s, r
        if best >= cutoff and (en.experiment_id not in exps or exps[en.experiment_id]["score"] < best):
            exp = session.get(Experiment, en.experiment_id)
            exps[exp.id] = {**experiment_summary(session, exp), "score": round(best, 3),
                            "match": f"{best_row.get('role')}: {best_row.get('name') or best_row.get('smiles')}",
                            "smiles": best_row.get("smiles")}
    key = lambda d: -d["score"]  # noqa: E731
    return {"experiments": sorted(exps.values(), key=key)[:limit], "materials": sorted(mats, key=key)[:limit]}


@router.get("/audit")
def audit_log(_: Admin, session: DB, limit: int = 200, before_id: int | None = None,
              object_type: str | None = None, username: str | None = None):
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    if object_type:
        q = q.where(AuditEvent.object_type == object_type)
    if username:
        q = q.where(AuditEvent.username == username)
    return [audit_out(ev) for ev in session.exec(q)]


@router.get("/audit/verify")
def audit_verify(_: CurrentUser, session: DB):
    return audit.verify_chain(session)
