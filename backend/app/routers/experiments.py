from __future__ import annotations

import copy
import json
import math
import re
import threading
import hashlib
import html
from typing import Any

import nh3

# Rich text allowed in procedure/notes sections: nh3's safe defaults, plus what the editor needs for
# checklists, alignment, text colour and links. Styles are limited to colour and alignment.
_ATTRS = {k: set(v) for k, v in nh3.ALLOWED_ATTRIBUTES.items()}
for _t in ("p", "h1", "h2", "h3", "h4", "span", "mark"):
    _ATTRS.setdefault(_t, set()).add("style")
_ATTRS.setdefault("ul", set()).add("data-type")
_ATTRS.setdefault("li", set()).update({"data-type", "data-checked"})
_ATTRS.setdefault("a", set()).update({"href", "target"})
_ATTRS.setdefault("th", set()).update({"colspan", "rowspan"})
_ATTRS.setdefault("td", set()).update({"colspan", "rowspan"})


def clean_html(html: str) -> str:
    return nh3.clean(html or "", tags=nh3.ALLOWED_TAGS | {"label"}, attributes=_ATTRS,
                     filter_style_properties={"color", "text-align", "background-color"}, link_rel="noopener noreferrer")
from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import select

from .. import audit, chem
from ..config import settings
from ..models import (
    Attachment, Entry, EntryKind, EntryVersion, Experiment, ExperimentStatus, Notebook, Role,
    Signature, User, utcnow,
)
from ..security import DB, CurrentUser, Writer, verify_password
from ..services import (
    content_hash, entry_out, experiment_summary, get_or_404, live_entries, read_experiment, require_editable,
    signatures_out, snapshot, user_brief,
)

router = APIRouter(prefix="/api", tags=["notebooks & experiments"])

# ====================================================================== notebooks


class NotebookIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    group_id: int | None = None
    visibility: str | None = None


def _check_audience(session, user: User, group_id: int | None, visibility: str | None) -> tuple[int | None, str]:
    """Validate a notebook's group/visibility: you can only give a notebook to a group you belong to."""
    from ..access import VISIBILITIES, access_for
    from ..models import LabGroup
    vis = visibility or ("group" if group_id else "everyone")
    if vis not in VISIBILITIES:
        raise HTTPException(422, f"visibility must be one of {', '.join(VISIBILITIES)}")
    if group_id is not None:
        get_or_404(session, LabGroup, group_id)
        if user.role != Role.admin and group_id not in access_for(session, user).groups:
            raise HTTPException(403, "You can only put a notebook in a group you belong to")
    elif vis == "group":
        raise HTTPException(422, "Choose a group for a group notebook")
    return group_id, vis


def notebook_out(session, nb: Notebook, exps: list[Experiment] | None = None) -> dict[str, Any]:
    from ..models import LabGroup
    g = session.get(LabGroup, nb.group_id) if nb.group_id else None
    out = {"id": nb.id, "name": nb.name, "description": nb.description, "archived": nb.archived,
           "owner": user_brief(session, nb.owner_id), "created_at": nb.created_at.isoformat() + "Z",
           "visibility": nb.visibility or "everyone", "group": {"id": g.id, "name": g.name} if g else None}
    if exps is not None:
        out["experiment_count"] = len(exps)
        out["status_counts"] = {s.value: sum(1 for e in exps if e.status == s) for s in ExperimentStatus}
    return out


@router.get("/notebooks")
def list_notebooks(user: CurrentUser, session: DB, include_archived: bool = False):
    from ..access import access_for
    acc = access_for(session, user)
    q = acc.filter_notebooks(select(Notebook).order_by(Notebook.name))
    if not include_archived:
        q = q.where(Notebook.archived == False)  # noqa: E712
    out = []
    for nb in session.exec(q):
        exps = session.exec(acc.filter(select(Experiment).where(Experiment.notebook_id == nb.id))).all()
        out.append(notebook_out(session, nb, exps))
    return out


@router.post("/notebooks", status_code=201)
def create_notebook(body: NotebookIn, user: Writer, session: DB):
    gid, vis = _check_audience(session, user, body.group_id, body.visibility)
    nb = Notebook(name=body.name, description=body.description, owner_id=user.id, group_id=gid, visibility=vis)
    session.add(nb)
    session.flush()
    audit.record(session, user, "create", "notebook", nb.id, name=nb.name, group_id=gid, visibility=vis)
    session.commit()
    return notebook_out(session, nb, [])


class NotebookPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    archived: bool | None = None
    group_id: int | None = None
    visibility: str | None = None


@router.patch("/notebooks/{nb_id}")
def update_notebook(nb_id: int, body: NotebookPatch, user: Writer, session: DB):
    nb = get_or_404(session, Notebook, nb_id)
    if nb.owner_id != user.id and user.role != Role.admin:
        raise HTTPException(403, "Only the notebook owner can change it")
    changes = body.model_dump(exclude_unset=True)
    if "group_id" in changes or "visibility" in changes:
        gid = changes["group_id"] if "group_id" in changes else nb.group_id
        vis = changes.get("visibility") or (nb.visibility if gid else None)
        if vis == "group" and not gid:
            vis = None
        changes["group_id"], changes["visibility"] = _check_audience(session, user, gid, vis)
    before = {k: getattr(nb, k) for k in changes}
    for k, v in changes.items():
        setattr(nb, k, v)
    session.add(nb)
    audit.record(session, user, "update", "notebook", nb.id, before=before, changes=changes)
    session.commit()
    return notebook_out(session, nb)


# ====================================================================== experiments


class ExperimentIn(BaseModel):
    title: str = Field(min_length=1)
    notebook_id: int | None = None
    description: str = ""
    template_id: int | None = None
    is_template: bool = False
    tags: list[str] = []


DEFAULT_CONTENT: dict[EntryKind, dict] = {
    EntryKind.text: {"html": ""},
    EntryKind.reaction: {"reaction_smiles": "", "rows": [], "totals": {}, "conditions": {}},
    EntryKind.table: {"columns": [{"name": "Sample"}, {"name": "Value"}], "rows": [["", ""]]},
    EntryKind.file: {"attachment_ids": [], "caption": ""},
    EntryKind.purification: {"method": "column", "stationary_phase": "", "eluent": "", "rf": "",
                             "rf_eluent": "", "isolated_mg": None, "appearance": "", "notes_html": ""},
    EntryKind.characterization: {"items": [{"technique": "1H NMR", "conditions": "", "data": "", "attachment_id": None}]},
    EntryKind.safety: {"controls": "", "waste": "", "emergency": "", "notes_html": "", "snapshot": None},
    EntryKind.screen: {"x_name": "Temperature (°C)", "y_name": "Catalyst loading (mol%)", "x_levels": ["25", "50", "80"],
                       "y_levels": ["1", "2.5", "5"], "response": "Yield (%)", "higher_is_better": True, "cells": {}},
}

DEFAULT_SECTIONS = [
    (EntryKind.text, "Aim", DEFAULT_CONTENT[EntryKind.text]),
    (EntryKind.reaction, "Reaction", DEFAULT_CONTENT[EntryKind.reaction]),
    (EntryKind.safety, "Safety & risk assessment", DEFAULT_CONTENT[EntryKind.safety]),
    (EntryKind.text, "Procedure", DEFAULT_CONTENT[EntryKind.text]),
    (EntryKind.purification, "Work-up & purification", DEFAULT_CONTENT[EntryKind.purification]),
    (EntryKind.characterization, "Characterisation", DEFAULT_CONTENT[EntryKind.characterization]),
    (EntryKind.text, "Observations & results", DEFAULT_CONTENT[EntryKind.text]),
]

METRIC_LABELS = (("atom_economy_pct", "atom economy", "%"), ("rme_pct", "RME", "%"), ("e_factor", "E-factor", ""),
                 ("pmi", "PMI", ""), ("solvent_l_per_kg", "solvent", " L/kg"))

CONDITION_FIELDS = ("temperature_c", "temperature_label", "time_h", "atmosphere", "target_conc_m", "notes")


def _clean_analysis(a) -> dict | None:
    """Saved spectrum integrations: [[ppm_a, ppm_b], ...] plus reference and peak threshold."""
    if not isinstance(a, dict):
        return None
    ints = []
    for pair in (a.get("integrals") or [])[:200]:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(_num(x) is not None for x in pair):
            ints.append([_num(pair[0]), _num(pair[1])])
    labels = []
    for lab in (a.get("labels") or [])[:len(ints)]:
        lab = lab if isinstance(lab, dict) else {}
        labels.append({"mult": str(lab.get("mult") or "")[:8], "j": str(lab.get("j") or "")[:40]})
    offset = _num(a.get("offset")) or 0.0
    out = {"integrals": ints, "ref_idx": int(_num(a.get("ref_idx")) or 0),
           "ref_value": _num(a.get("ref_value")) or 1.0, "threshold": _num(a.get("threshold")) or 0.05}
    if labels and any(lab["mult"] or lab["j"] for lab in labels):
        out["labels"] = labels + [{"mult": "", "j": ""}] * (len(ints) - len(labels))
    if offset and abs(offset) <= 20:
        out["offset"] = offset
    imps = []
    for imp in (a.get("impurities") or [])[:20]:
        if not isinstance(imp, dict):
            continue
        imps.append({"short": str(imp.get("short", ""))[:20], "name": str(imp.get("name", ""))[:60],
                     "molPct": _num(imp.get("molPct")), "wtPct": _num(imp.get("wtPct")),
                     "signals": [v for v in (_num(x) for x in (imp.get("signals") or [])[:6]) if v is not None]})
    if imps:
        out["impurities"] = imps
    fid = a.get("fid")
    if isinstance(fid, dict):  # phase/line-broadening chosen for a raw FID processed in the browser
        ph0, ph1, lb = _num(fid.get("ph0")), _num(fid.get("ph1")), _num(fid.get("lb"))
        if ph0 is not None and ph1 is not None:
            out["fid"] = {"ph0": round(ph0 % 360, 2), "ph1": round(max(-1440.0, min(1440.0, ph1)), 2),
                          "lb": round(max(0.0, min(50.0, lb)), 2) if lb is not None else None}
    overlays = [int(v) for v in (a.get("overlays") or [])[:4] if isinstance(v, (int, float))]
    if overlays:
        out["overlays"] = overlays
        out["overlay_mode"] = "overlay" if a.get("overlay_mode") == "overlay" else "stack"
    return out


def _num(v):
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


@router.get("/experiments")
def list_experiments(
    user: CurrentUser, session: DB,
    notebook_id: int | None = None, status_: ExperimentStatus | None = Query(None, alias="status"),
    owner_id: int | None = None, templates: bool = False, limit: int = 200,
):
    from ..access import visible_experiment_filter
    q = visible_experiment_filter(select(Experiment).where(Experiment.is_template == templates), session, user)
    if notebook_id is not None:
        q = q.where(Experiment.notebook_id == notebook_id)
    if status_ is not None:
        q = q.where(Experiment.status == status_)
    if owner_id is not None:
        q = q.where(Experiment.owner_id == owner_id)
    q = q.order_by(Experiment.updated_at.desc()).limit(limit)
    return [experiment_summary(session, e) for e in session.exec(q)]


def _new_entry(session, exp_id: int, kind: EntryKind, title: str, content: dict, pos: int, user: User):
    e = Entry(experiment_id=exp_id, kind=kind, title=title, content=content, position=pos,
              updated_by=user.id)
    session.add(e)
    session.flush()
    session.add(EntryVersion(entry_id=e.id, version=1, title=title, content=content, created_by=user.id))
    return e


def _require_notebook(session, user: User, nb_id: int) -> Notebook:
    from ..access import access_for
    nb = get_or_404(session, Notebook, nb_id)
    if not access_for(session, user).can_read_notebook(nb):
        raise HTTPException(404, f"Notebook {nb_id} not found")
    return nb


@router.post("/experiments", status_code=201)
def create_experiment(body: ExperimentIn, user: Writer, session: DB):
    if body.notebook_id is not None:
        _require_notebook(session, user, body.notebook_id)
    elif not body.is_template:
        raise HTTPException(400, "notebook_id is required")
    exp = Experiment(title=body.title, notebook_id=body.notebook_id, description=body.description,
                     owner_id=user.id, is_template=body.is_template, template_id=body.template_id,
                     tags=body.tags)
    session.add(exp)
    session.flush()
    if body.template_id:
        tpl = read_experiment(session, user, body.template_id)
        sections = [(e.kind, e.title, copy.deepcopy(e.content)) for e in live_entries(session, tpl.id)]
    else:
        sections = DEFAULT_SECTIONS
    for i, (kind, title, content) in enumerate(sections):
        _new_entry(session, exp.id, kind, title, copy.deepcopy(content), i, user)
    audit.record(session, user, "create", "experiment", exp.id, eid=exp.eid, title=exp.title,
                 template_id=body.template_id)
    session.commit()
    return experiment_summary(session, exp)


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: int, user: CurrentUser, session: DB):
    exp = read_experiment(session, user, exp_id)
    atts = session.exec(select(Attachment).where(Attachment.experiment_id == exp.id))
    return {
        **experiment_summary(session, exp),
        "entries": [entry_out(e) for e in live_entries(session, exp.id)],
        "attachments": [attachment_out(a) for a in atts],
        "signatures": signatures_out(session, exp.id),
        **_access_info(session, user, exp),
    }


def _access_info(session, user: User, exp: Experiment) -> dict[str, Any]:
    from ..access import access_for
    from ..models import Comment, LabGroup
    acc = access_for(session, user)
    nb = session.get(Notebook, exp.notebook_id) if exp.notebook_id else None
    g = session.get(LabGroup, nb.group_id) if nb and nb.group_id else None
    open_threads = session.exec(select(Comment.id).where(Comment.experiment_id == exp.id, Comment.parent_id == None,  # noqa: E711
                                                         Comment.deleted == False, Comment.resolved_at == None)).all()  # noqa: E712
    return {
        "can_edit": acc.can_edit(exp),
        "shared_with_me": acc.shares.get(exp.id),
        "audience": {"visibility": (nb.visibility or "everyone") if nb else ("everyone" if exp.is_template else "private"),
                     "group": {"id": g.id, "name": g.name} if g else None},
        "open_comments": len(open_threads),
    }


class ExperimentPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    notebook_id: int | None = None


@router.patch("/experiments/{exp_id}")
def update_experiment(exp_id: int, body: ExperimentPatch, user: Writer, session: DB):
    exp = read_experiment(session, user, exp_id)
    require_editable(exp, user)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("notebook_id") not in (None, exp.notebook_id):
        if exp.owner_id != user.id and user.role != Role.admin:
            raise HTTPException(403, "Only the owner can move an experiment to another notebook")
        _require_notebook(session, user, changes["notebook_id"])
    elif "notebook_id" in changes:
        changes.pop("notebook_id")
    before = {k: getattr(exp, k) for k in changes}
    for k, v in changes.items():
        setattr(exp, k, v)
    exp.updated_at = utcnow()
    session.add(exp)
    audit.record(session, user, "update", "experiment", exp.id, before=before, after=changes)
    session.commit()
    return experiment_summary(session, exp)


@router.post("/experiments/{exp_id}/clone", status_code=201)
def clone_experiment(exp_id: int, user: Writer, session: DB, as_template: bool = False, scale: float | None = None):
    src = read_experiment(session, user, exp_id)
    if scale is not None and not (0.001 <= scale <= 1000):
        raise HTTPException(400, "Scale factor must be between 0.001 and 1000")
    from ..access import access_for
    nb = session.get(Notebook, src.notebook_id) if src.notebook_id else None
    if not as_template and src.notebook_id is not None and (nb is None or not access_for(session, user).can_read_notebook(nb)):
        raise HTTPException(403, "You can copy this experiment as a template, but not into a notebook you can't open")
    prefix = "Template: " if as_template else (f"×{scale:g} scale: " if scale else "Copy of ")
    exp = Experiment(
        title=prefix + src.title,
        notebook_id=None if as_template else src.notebook_id, description=src.description,
        owner_id=user.id, is_template=as_template, tags=list(src.tags or []),
    )
    session.add(exp)
    session.flush()
    for e in live_entries(session, src.id):
        content = copy.deepcopy(e.content)
        if e.kind == EntryKind.file:
            content = {"attachment_ids": [], "caption": content.get("caption", "")}
        if scale:
            content = _scaled(e.kind, content, scale)
        _new_entry(session, exp.id, e.kind, e.title, content, e.position, user)
    audit.record(session, user, "clone", "experiment", exp.id, source=src.eid, as_template=as_template, scale=scale)
    session.commit()
    return experiment_summary(session, exp)


def _scaled(kind: EntryKind, content: dict[str, Any], f: float) -> dict[str, Any]:
    """Content for a repeat at f× scale: typed amounts scale, equivalents and concentrations don't,
    fill-to-concentration solvents recalculate, and results (masses, spectra) start empty."""
    if kind == EntryKind.reaction:
        rows = []
        for r in content.get("rows") or []:
            r = dict(r)
            if r.get("role") == "product":
                r["actual_mass_mg"] = r["yield_pct"] = None
            else:
                b = r.get("basis")
                key = {"mass": "mass_mg", "volume": "volume_ml", "mmol": "mmol"}.get(b)
                if r.get("role") == "solvent" and b != "fill":
                    key = "volume_ml"
                if key and _num(r.get(key)) is not None:
                    r[key] = round(_num(r[key]) * f, 6)
            r["lot_id"] = None  # pick lots again for the new batch
            rows.append(r)
        content = {**content, "rows": rows}
        return _clean_content(kind, content)
    if kind == EntryKind.purification:
        return {**content, "isolated_mg": None}
    if kind == EntryKind.characterization:
        return {"items": [{"technique": it.get("technique", ""), "conditions": it.get("conditions", ""), "data": "", "attachment_id": None}
                          for it in content.get("items") or []]}
    if kind == EntryKind.safety:
        return {**content, "snapshot": None}  # amounts changed: reassess
    return content


# ====================================================================== entries


class EntryIn(BaseModel):
    kind: EntryKind
    title: str = ""
    content: dict[str, Any] = {}
    position: int | None = None


def _clean_content(kind: EntryKind, content: dict[str, Any]) -> dict[str, Any]:
    content = dict(content or {})
    if kind == EntryKind.text:
        content["html"] = clean_html(content.get("html", ""))
    elif kind == EntryKind.reaction:
        cond = {k: (content.get("conditions") or {}).get(k) for k in CONDITION_FIELDS}
        for k in ("temperature_c", "time_h", "target_conc_m"):
            cond[k] = _num(cond[k])
        content["conditions"] = cond
        rows = content.get("rows") or []
        if rows:
            calc = chem.compute_stoichiometry(rows, cond["target_conc_m"])
            content["rows"], content["totals"] = calc["rows"], calc["totals"]
            content["warnings"] = calc["warnings"]
    elif kind == EntryKind.purification:
        content["notes_html"] = clean_html(content.get("notes_html", ""))
        content["isolated_mg"] = _num(content.get("isolated_mg"))
    elif kind == EntryKind.characterization:
        items = []
        for it in content.get("items") or []:
            item = {"technique": str(it.get("technique", "")), "conditions": str(it.get("conditions", "")),
                    "data": str(it.get("data", "")), "attachment_id": it.get("attachment_id")}
            analysis = _clean_analysis(it.get("analysis"))
            if analysis:
                item["analysis"] = analysis
            items.append(item)
        content["items"] = items
    elif kind == EntryKind.safety:
        for k in ("controls", "waste", "emergency"):
            content[k] = str(content.get(k) or "")[:4000]
        content["notes_html"] = clean_html(content.get("notes_html", ""))
        snap = content.get("snapshot")
        content["snapshot"] = snap if isinstance(snap, dict) and len(json.dumps(snap)) < 200_000 else None
    elif kind == EntryKind.screen:
        def levels(v):
            return [str(x)[:40] for x in v if x is not None and not isinstance(x, (dict, list))][:12] if isinstance(v, list) else []
        content["x_name"], content["y_name"] = str(content.get("x_name") or "")[:80], str(content.get("y_name") or "")[:80]
        content["x_levels"], content["y_levels"] = levels(content.get("x_levels")), levels(content.get("y_levels")) or [""]
        content["response"] = str(content.get("response") or "Yield (%)")[:60]
        content["higher_is_better"] = bool(content.get("higher_is_better", True))
        cells = {}
        raw = content.get("cells")
        for k, c in (raw.items() if isinstance(raw, dict) else []):
            try:
                i, j = (int(x) for x in str(k).split(","))
            except ValueError:
                continue
            if 0 <= i < len(content["y_levels"]) and 0 <= j < len(content["x_levels"]) and isinstance(c, dict):
                cell = {"value": _num(c.get("value")), "note": str(c.get("note") or "")[:200]}
                if isinstance(c.get("experiment_id"), int):
                    cell["experiment_id"] = c["experiment_id"]
                if cell["value"] is not None or cell["note"] or "experiment_id" in cell:
                    cells[f"{i},{j}"] = cell
        content["cells"] = cells
    elif kind == EntryKind.table:
        content.setdefault("columns", [])
        content.setdefault("rows", [])
    elif kind == EntryKind.file:
        content.setdefault("attachment_ids", [])
        analyses = {str(k): _clean_analysis(v) for k, v in (content.get("analyses") or {}).items()}
        content["analyses"] = {k: v for k, v in analyses.items() if v}
    return content


_entry_lock = threading.Lock()  # positions are read-then-written; serialise adds and reorders


def _clean_filename(name: str | None) -> str:
    """Just the file's own name: no folders, no control characters."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip().lstrip(".") or "file"
    return base[:200]


@router.post("/experiments/{exp_id}/entries", status_code=201)
def add_entry(exp_id: int, body: EntryIn, user: Writer, session: DB):
    with _entry_lock:
        return _add_entry(exp_id, body, user, session)


def _add_entry(exp_id: int, body: EntryIn, user, session) -> dict[str, Any]:
    exp = read_experiment(session, user, exp_id)
    require_editable(exp, user)
    existing = live_entries(session, exp.id)
    pos = body.position if body.position is not None else (max((e.position for e in existing), default=-1) + 1)
    for e in existing:
        if e.position >= pos:
            e.position += 1
            session.add(e)
    content = _clean_content(body.kind, body.content or copy.deepcopy(DEFAULT_CONTENT[body.kind]))
    e = _new_entry(session, exp.id, body.kind, body.title or body.kind.value.title(), content, pos, user)
    exp.updated_at = utcnow()
    session.add(exp)
    audit.record(session, user, "create", "entry", e.id, experiment=exp.eid, kind=e.kind.value, title=e.title)
    session.commit()
    return entry_out(e)


class EntryUpdate(BaseModel):
    title: str | None = None
    content: dict[str, Any] | None = None
    expected_version: int | None = None


@router.put("/entries/{entry_id}")
def update_entry(entry_id: int, body: EntryUpdate, user: Writer, session: DB):
    e = get_or_404(session, Entry, entry_id)
    exp = read_experiment(session, user, e.experiment_id)
    require_editable(exp, user)
    if body.expected_version is not None and body.expected_version != e.version:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Entry was changed elsewhere (now v{e.version}); reload before saving")
    new_title = body.title if body.title is not None else e.title
    new_content = _clean_content(e.kind, body.content) if body.content is not None else e.content
    if new_title == e.title and audit.canonical(new_content) == audit.canonical(e.content):
        return entry_out(e)
    e.title, e.content = new_title, new_content
    e.version += 1
    e.updated_by, e.updated_at = user.id, utcnow()
    exp.updated_at = e.updated_at
    session.add_all([e, exp, EntryVersion(entry_id=e.id, version=e.version, title=e.title,
                                          content=new_content, created_by=user.id)])
    audit.record(session, user, "update", "entry", e.id, experiment=exp.eid, version=e.version,
                 content_sha256=hashlib.sha256(audit.canonical(new_content).encode()).hexdigest())
    session.commit()
    return entry_out(e)


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, user: Writer, session: DB):
    """Soft delete: the entry and every prior version remain in the history."""
    e = get_or_404(session, Entry, entry_id)
    exp = read_experiment(session, user, e.experiment_id)
    require_editable(exp, user)
    e.deleted = True
    e.version += 1
    session.add_all([e, EntryVersion(entry_id=e.id, version=e.version, title=e.title,
                                     content=e.content, deleted=True, created_by=user.id)])
    audit.record(session, user, "delete", "entry", e.id, experiment=exp.eid, title=e.title)
    session.commit()
    return {"ok": True}


class ReorderIn(BaseModel):
    entry_ids: list[int]


@router.post("/experiments/{exp_id}/reorder")
def reorder_entries(exp_id: int, body: ReorderIn, user: Writer, session: DB):
    exp = read_experiment(session, user, exp_id)
    require_editable(exp, user)
    entries = {e.id: e for e in live_entries(session, exp.id)}
    if set(body.entry_ids) != set(entries):
        raise HTTPException(400, "entry_ids must list every entry exactly once")
    for i, eid in enumerate(body.entry_ids):
        entries[eid].position = i
        session.add(entries[eid])
    audit.record(session, user, "reorder", "experiment", exp.id, order=body.entry_ids)
    session.commit()
    return {"ok": True}


@router.get("/entries/{entry_id}/versions")
def entry_versions(entry_id: int, user: CurrentUser, session: DB):
    read_experiment(session, user, get_or_404(session, Entry, entry_id).experiment_id)
    vs = session.exec(select(EntryVersion).where(EntryVersion.entry_id == entry_id)
                      .order_by(EntryVersion.version.desc()))
    return [{"version": v.version, "title": v.title, "content": v.content, "deleted": v.deleted,
             "created_at": v.created_at.isoformat() + "Z", "created_by": user_brief(session, v.created_by)}
            for v in vs]


# ====================================================================== attachments


def attachment_out(a: Attachment) -> dict[str, Any]:
    return {"id": a.id, "filename": a.filename, "content_type": a.content_type, "size": a.size,
            "sha256": a.sha256, "created_at": a.created_at.isoformat() + "Z"}


def _blob_path(sha: str):
    return settings.attachments_dir / sha[:2] / sha


@router.post("/experiments/{exp_id}/attachments", status_code=201)
async def upload_attachment(exp_id: int, user: Writer, session: DB, file: UploadFile = File(...)):
    exp = read_experiment(session, user, exp_id)
    require_editable(exp, user)
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File larger than {settings.max_upload_mb} MB")
    sha = hashlib.sha256(data).hexdigest()
    path = _blob_path(sha)
    if not path.exists():  # content-addressed, deduplicated, write-once
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    a = Attachment(experiment_id=exp.id, filename=_clean_filename(file.filename),
                   content_type=file.content_type or "application/octet-stream",
                   size=len(data), sha256=sha, uploaded_by=user.id)
    session.add(a)
    session.flush()
    audit.record(session, user, "upload", "attachment", a.id, experiment=exp.eid,
                 filename=a.filename, sha256=sha, size=a.size)
    session.commit()
    return attachment_out(a)


@router.get("/attachments/{att_id}")
def download_attachment(att_id: int, user: CurrentUser, session: DB, inline: bool = False):
    a = get_or_404(session, Attachment, att_id)
    read_experiment(session, user, a.experiment_id)
    path = _blob_path(a.sha256)
    if not path.exists():
        raise HTTPException(410, "Attachment blob missing from storage")
    if hashlib.sha256(path.read_bytes()).hexdigest() != a.sha256:
        raise HTTPException(500, "Attachment failed integrity check")
    return FileResponse(path, media_type=a.content_type, filename=a.filename,
                        content_disposition_type="inline" if inline else "attachment")


# ====================================================================== signing workflow


class SignIn(BaseModel):
    password: str
    comment: str = ""


def _check_password(user: User, password: str, session) -> None:
    from ..security import has_password
    if not has_password(user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Signing needs a password. You sign in with single sign-on, so set a signing password in Account settings first.")
    from ..security import MAX_PER_ACCOUNT, check_throttle, clear_failures, record_failure
    key = f"sign:{user.id}"
    check_throttle(session, (key, MAX_PER_ACCOUNT))
    if not verify_password(password, user.password_hash):
        record_failure(session, key)
        audit.record(session, user, "signature_failed", "user", user.id)
        session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password re-entry failed; signature not applied")
    clear_failures(session, key)


def _sign(session, exp: Experiment, user: User, meaning: str, comment: str) -> Signature:
    sig = Signature(experiment_id=exp.id, user_id=user.id, meaning=meaning, comment=comment,
                    content_hash=content_hash(session, exp), signer_name=user.full_name or user.username,
                    signer_username=user.username)
    session.add(sig)
    session.flush()
    audit.record(session, user, f"sign:{meaning}", "experiment", exp.id, eid=exp.eid,
                 content_hash=sig.content_hash, comment=comment)
    return sig


@router.post("/experiments/{exp_id}/sign")
def sign_experiment(exp_id: int, body: SignIn, user: Writer, session: DB):
    """Author signature: locks the record and sends it for witnessing."""
    exp = read_experiment(session, user, exp_id)
    if exp.is_template:
        raise HTTPException(400, "Templates are not signed")
    if exp.status != ExperimentStatus.open:
        raise HTTPException(409, f"{exp.eid} is already {exp.status.value}")
    if exp.owner_id != user.id:
        raise HTTPException(403, "Only the author can sign as author")
    _check_password(user, body.password, session)
    _sign(session, exp, user, "author", body.comment)
    exp.status = ExperimentStatus.signed
    exp.updated_at = utcnow()
    session.add(exp)
    session.commit()
    return get_experiment(exp.id, user, session)


def _author_sig(session, exp: Experiment) -> Signature | None:
    return session.exec(select(Signature).where(
        Signature.experiment_id == exp.id, Signature.meaning == "author").order_by(Signature.id.desc())).first()


@router.post("/experiments/{exp_id}/witness")
def witness_experiment(exp_id: int, body: SignIn, user: Writer, session: DB):
    exp = read_experiment(session, user, exp_id)
    if exp.status != ExperimentStatus.signed:
        raise HTTPException(409, "Only author-signed experiments can be witnessed")
    if exp.owner_id == user.id:
        raise HTTPException(403, "The author cannot witness their own experiment")
    _check_password(user, body.password, session)
    author = _author_sig(session, exp)
    if author is None or author.content_hash != content_hash(session, exp):
        raise HTTPException(409, "Record content no longer matches the author signature")
    _sign(session, exp, user, "witness", body.comment)
    exp.status = ExperimentStatus.witnessed
    exp.updated_at = utcnow()
    session.add(exp)
    session.commit()
    return get_experiment(exp.id, user, session)


@router.post("/experiments/{exp_id}/reject")
def reject_experiment(exp_id: int, body: SignIn, user: Writer, session: DB):
    """Witness declines; record returns to the author as open, with the reason recorded."""
    exp = read_experiment(session, user, exp_id)
    if exp.status != ExperimentStatus.signed:
        raise HTTPException(409, "Only experiments awaiting witness can be rejected")
    if exp.owner_id == user.id:
        raise HTTPException(403, "Use 'withdraw' instead; authors cannot reject their own records")
    if not body.comment.strip():
        raise HTTPException(400, "A reason is required")
    _check_password(user, body.password, session)
    _sign(session, exp, user, "rejected", body.comment)
    exp.status = ExperimentStatus.open
    exp.updated_at = utcnow()
    session.add(exp)
    session.commit()
    return get_experiment(exp.id, user, session)


@router.post("/experiments/{exp_id}/withdraw")
def withdraw_signature(exp_id: int, body: SignIn, user: Writer, session: DB):
    """Author pulls a signed (not yet witnessed) record back to open."""
    exp = read_experiment(session, user, exp_id)
    if exp.status != ExperimentStatus.signed or exp.owner_id != user.id:
        raise HTTPException(409, "Only the author can withdraw a record awaiting witness")
    _check_password(user, body.password, session)
    _sign(session, exp, user, "withdrawn", body.comment)
    exp.status = ExperimentStatus.open
    session.add(exp)
    session.commit()
    return get_experiment(exp.id, user, session)


@router.post("/experiments/{exp_id}/void")
def void_experiment(exp_id: int, body: SignIn, user: Writer, session: DB):
    exp = read_experiment(session, user, exp_id)
    if exp.owner_id != user.id and user.role != Role.admin:
        raise HTTPException(403, "Only the author or an admin can void")
    if exp.status == ExperimentStatus.voided:
        raise HTTPException(409, "Already voided")
    if not body.comment.strip():
        raise HTTPException(400, "A reason is required")
    _check_password(user, body.password, session)
    _sign(session, exp, user, "void", body.comment)
    exp.status = ExperimentStatus.voided
    session.add(exp)
    session.commit()
    return get_experiment(exp.id, user, session)


@router.get("/experiments/{exp_id}/verify")
def verify_experiment(exp_id: int, user: CurrentUser, session: DB):
    exp = read_experiment(session, user, exp_id)
    current = content_hash(session, exp)
    sigs = signatures_out(session, exp.id)
    for s in sigs:
        s["matches_current"] = s["content_hash"] == current
    attestations = [s for s in sigs if s["meaning"] in ("author", "witness")]
    latest = attestations[-1] if attestations else None
    return {"content_hash": current, "signatures": sigs,
            "intact": latest is None or exp.status == ExperimentStatus.open or latest["matches_current"]}


@router.get("/experiments/{exp_id}/writeup")
def experiment_writeup(exp_id: int, user: CurrentUser, session: DB):
    from .. import writeup

    exp = read_experiment(session, user, exp_id)
    return writeup.draft(live_entries(session, exp.id))


@router.get("/experiments/{exp_id}/audit")
def experiment_audit(exp_id: int, user: CurrentUser, session: DB):
    exp = read_experiment(session, user, exp_id)
    entry_ids = [e.id for e in session.exec(select(Entry).where(Entry.experiment_id == exp.id))]
    att_ids = [a.id for a in session.exec(select(Attachment).where(Attachment.experiment_id == exp.id))]
    from ..models import AuditEvent

    q = select(AuditEvent).where(
        ((AuditEvent.object_type == "experiment") & (AuditEvent.object_id == exp.id))
        | ((AuditEvent.object_type == "entry") & (AuditEvent.object_id.in_(entry_ids or [-1])))
        | ((AuditEvent.object_type == "attachment") & (AuditEvent.object_id.in_(att_ids or [-1])))
    ).order_by(AuditEvent.id.desc())
    return [audit_out(ev) for ev in session.exec(q)]


def audit_out(ev) -> dict[str, Any]:
    return {"id": ev.id, "ts": ev.ts.isoformat() + "Z", "username": ev.username, "action": ev.action,
            "object_type": ev.object_type, "object_id": ev.object_id, "details": ev.details,
            "hash": ev.hash}


# ====================================================================== export


PURIFICATION_LABELS = {
    "column": "Flash column chromatography", "prep_tlc": "Preparative TLC", "prep_hplc": "Preparative HPLC",
    "recrystallization": "Recrystallisation", "trituration": "Trituration", "distillation": "Distillation",
    "extraction": "Extraction only", "none": "Used crude",
}


def _fmt(v, nd=3):
    if v is None or v == "":
        return ""
    if isinstance(v, float):
        return f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return html.escape(str(v))


def _render_entry(e: Entry, atts: dict[int, Attachment]) -> str:
    c = e.content or {}
    h = f"<section><h2>{html.escape(e.title)}</h2>"
    if e.kind == EntryKind.text:
        h += f"<div class='rich'>{clean_html(c.get('html', ''))}</div>"
    elif e.kind == EntryKind.reaction:
        cond = c.get("conditions") or {}
        bits = []
        if cond.get("temperature_c") is not None or cond.get("temperature_label"):
            t = f"{_fmt(cond.get('temperature_c'))} °C" if cond.get("temperature_c") is not None else ""
            bits.append(" ".join(x for x in (t, html.escape(cond.get("temperature_label") or "")) if x))
        if cond.get("time_h") is not None:
            bits.append(f"{_fmt(cond['time_h'])} h")
        if cond.get("atmosphere"):
            bits.append(html.escape(cond["atmosphere"]))
        if cond.get("target_conc_m"):
            bits.append(f"{_fmt(cond['target_conc_m'])} M")
        if bits:
            h += f"<p><strong>Conditions:</strong> {' · '.join(bits)}</p>"
        if cond.get("notes"):
            h += f"<p>{html.escape(cond['notes'])}</p>"
        if c.get("reaction_smiles"):
            try:
                h += f"<div class='rxn'>{chem.depict_reaction_svg(c['reaction_smiles'])}</div>"
            except chem.ChemError:
                h += f"<p><code>{html.escape(c['reaction_smiles'])}</code></p>"
        cols = [("role", "Role"), ("name", "Name"), ("formula", "Formula"), ("mw", "MW"),
                ("equiv", "Equiv"), ("mmol", "mmol"), ("mass_mg", "Mass (mg)"),
                ("volume_ml", "Vol (mL)"), ("theoretical_mass_mg", "Theor. (mg)"),
                ("actual_mass_mg", "Actual (mg)"), ("yield_pct", "Yield %")]
        h += "<table><tr>" + "".join(f"<th>{t}</th>" for _, t in cols) + "</tr>"
        for r in c.get("rows", []):
            h += "<tr>" + "".join(
                f"<td>{_fmt(r.get(k))}{' ★' if k == 'role' and r.get('limiting') else ''}</td>"
                for k, _ in cols) + "</tr>"
        h += "</table>"
        m = chem.green_metrics([chem.new_row(**r) for r in c.get("rows", [])]) if c.get("rows") else {}
        parts = [f"{label} {_fmt(m[k])}{unit}" for k, label, unit in METRIC_LABELS if m.get(k) is not None]
        if parts:
            h += f"<p><strong>Green metrics</strong> ({html.escape(m['scope'])}): {' · '.join(parts)}</p>"
    elif e.kind == EntryKind.safety:
        snap = c.get("snapshot") or {}
        if snap.get("chemicals"):
            h += f"<p>Hazard data recorded {html.escape(str(snap.get('taken_at', ''))[:10])} from PubChem.</p>"
            h += "<table><tr><th>Chemical</th><th>Signal</th><th>Hazard statements</th><th>Source</th></tr>"
            for ch in snap["chemicals"]:
                h += (f"<tr><td>{html.escape(ch.get('name', ''))}</td><td>{html.escape(ch.get('signal', ''))}</td>"
                      f"<td>{html.escape('; '.join(ch.get('hazards') or []))}</td><td>{html.escape(ch.get('source', ''))}</td></tr>")
            h += "</table>"
            if snap.get("warnings"):
                h += "<ul>" + "".join(f"<li>{html.escape(w.get('text', ''))}</li>" for w in snap["warnings"]) + "</ul>"
        for k, label in (("controls", "Controls"), ("waste", "Waste"), ("emergency", "Emergency")):
            if c.get(k):
                h += f"<p><strong>{label}:</strong> {html.escape(c[k])}</p>"
        h += f"<div class='rich'>{clean_html(c.get('notes_html', ''))}</div>"
    elif e.kind == EntryKind.screen:
        xs, ys, cells = c.get("x_levels") or [], c.get("y_levels") or [], c.get("cells") or {}
        h += f"<p>{html.escape(c.get('response', ''))} by {html.escape(c.get('y_name', ''))} (rows) and {html.escape(c.get('x_name', ''))} (columns)</p>"
        h += "<table><tr><th></th>" + "".join(f"<th>{html.escape(x)}</th>" for x in xs) + "</tr>"
        for i, yv in enumerate(ys):
            h += f"<tr><th>{html.escape(yv)}</th>" + "".join(
                f"<td>{_fmt((cells.get(f'{i},{j}') or {}).get('value'))}</td>" for j in range(len(xs))) + "</tr>"
        h += "</table>"
    elif e.kind == EntryKind.table:
        cols = c.get("columns", [])
        h += "<table><tr>" + "".join(
            f"<th>{html.escape(col.get('name', ''))}{(' (' + html.escape(col['unit']) + ')') if col.get('unit') else ''}</th>"
            for col in cols) + "</tr>"
        for row in c.get("rows", []):
            h += "<tr>" + "".join(f"<td>{_fmt(v)}</td>" for v in row) + "</tr>"
        h += "</table>"
    elif e.kind == EntryKind.purification:
        rows = [("Method", PURIFICATION_LABELS.get(c.get("method"), c.get("method") or "")),
                ("Stationary phase", c.get("stationary_phase")), ("Eluent", c.get("eluent")),
                ("Rf", f"{c.get('rf') or ''} {('(' + c['rf_eluent'] + ')') if c.get('rf_eluent') else ''}".strip()),
                ("Isolated", f"{_fmt(c.get('isolated_mg'))} mg" if c.get("isolated_mg") is not None else ""),
                ("Appearance", c.get("appearance"))]
        h += "<table>" + "".join(f"<tr><th>{k}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows if v) + "</table>"
        h += f"<div class='rich'>{clean_html(c.get('notes_html', ''))}</div>"
    elif e.kind == EntryKind.characterization:
        h += "<table><tr><th>Technique</th><th>Conditions</th><th>Data</th><th>File</th></tr>"
        for it in c.get("items", []):
            a = atts.get(it.get("attachment_id"))
            h += (f"<tr><td>{html.escape(it.get('technique', ''))}</td><td>{html.escape(it.get('conditions', ''))}</td>"
                  f"<td>{html.escape(it.get('data', ''))}</td><td>{html.escape(a.filename) if a else ''}</td></tr>")
        h += "</table>"
    elif e.kind == EntryKind.file:
        h += "<ul>" + "".join(
            f"<li>{html.escape(atts[i].filename)} — {atts[i].size:,} bytes — sha256 <code>{atts[i].sha256}</code></li>"
            for i in c.get("attachment_ids", []) if i in atts) + "</ul>"
        if c.get("caption"):
            h += f"<p>{html.escape(c['caption'])}</p>"
    return h + "</section>"


@router.get("/experiments/{exp_id}/export")
def export_experiment(exp_id: int, user: CurrentUser, session: DB, format: str = "html"):
    exp = read_experiment(session, user, exp_id)
    if format == "md":
        from ..export_md import experiment_markdown, safe_name

        md = experiment_markdown(session, exp)
        return Response(md, media_type="text/markdown; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{exp.eid} {safe_name(exp.title)}.md"'.encode("latin-1", "replace").decode("latin-1")})
    if format == "json":
        return {"experiment": experiment_summary(session, exp), "snapshot": snapshot(session, exp),
                "content_hash": content_hash(session, exp), "signatures": signatures_out(session, exp.id)}
    atts = {a.id: a for a in session.exec(select(Attachment).where(Attachment.experiment_id == exp.id))}
    owner = session.get(User, exp.owner_id)
    sigs = signatures_out(session, exp.id)
    body = "".join(_render_entry(e, atts) for e in live_entries(session, exp.id))
    sig_html = "".join(
        f"<tr><td>{html.escape(s['meaning'])}</td><td>{html.escape(s['signer_name'] or s['user']['username'])}</td>"
        f"<td>{s['signed_at']}</td><td>{html.escape(s['comment'])}</td><td><code>{s['content_hash'][:16]}…</code></td></tr>"
        for s in sigs) or "<tr><td colspan=5>Not signed</td></tr>"
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{exp.eid} {html.escape(exp.title)}</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;max-width:900px;margin:2rem auto;color:#111}}
header{{border-bottom:2px solid #111;margin-bottom:1rem}} h1{{margin:.2rem 0}} .meta{{color:#555}}
table{{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:12px}} td,th{{border:1px solid #bbb;padding:3px 6px;text-align:left}}
th{{background:#f2f2f2}} section{{margin:1.5rem 0;page-break-inside:avoid}} .rxn svg{{max-width:100%;height:auto}}
code{{font-size:11px}} ul[data-type=taskList]{{list-style:none;padding-left:.2em}} li[data-type=taskItem]::before{{content:'\\2610  '}}
li[data-checked=true]::before{{content:'\\2611  '}} li[data-type=taskItem] div,li[data-type=taskItem] p{{display:inline}} blockquote{{border-left:3px solid #999;margin:.5rem 0;padding:.2rem .8rem;background:#f6f6f6}} mark{{background:#fff3a0}}
footer{{margin-top:2rem;border-top:1px solid #999;font-size:11px;color:#555}}</style></head><body>
<header><div class="meta">{exp.eid} · {html.escape(exp.status.value.upper())}</div><h1>{html.escape(exp.title)}</h1>
<div class="meta">Author: {html.escape(owner.full_name or owner.username)} · Created {exp.created_at:%Y-%m-%d %H:%M} UTC · Last modified {exp.updated_at:%Y-%m-%d %H:%M} UTC</div>
<p>{html.escape(exp.description)}</p></header>{body}
<section><h2>Signatures</h2><table><tr><th>Meaning</th><th>Signer</th><th>Signed (UTC)</th><th>Comment</th><th>Content hash</th></tr>{sig_html}</table></section>
<footer>Record content hash (SHA-256): <code>{content_hash(session, exp)}</code><br>Generated by Benchtop ELN {utcnow():%Y-%m-%d %H:%M} UTC</footer>
</body></html>"""
    return HTMLResponse(doc)


SPECTRUM_RE = re.compile(r"\.(jdx|dx|jcamp|jcm|csv|tsv|txt|xy|zip)$", re.I)


@router.get("/spectra")
def list_spectra(user: CurrentUser, session: DB, q: str = "", near: int | None = None, limit: int = 60):
    """Attachments that look like spectra, for overlays. `near` (an attachment id) puts its experiment first."""
    home = session.get(Attachment, near).experiment_id if near and session.get(Attachment, near) else None
    from ..access import visible_experiment_filter
    rows = session.exec(visible_experiment_filter(select(Attachment, Experiment).join(Experiment, Experiment.id == Attachment.experiment_id)
                        .order_by(Attachment.created_at.desc()), session, user)).all()
    ql = q.lower().strip()
    out = []
    for a, e in rows:
        if not SPECTRUM_RE.search(a.filename) or (near and a.id == near):
            continue
        if ql and ql not in f"{a.filename} {e.eid} {e.title}".lower():
            continue
        out.append({"id": a.id, "filename": a.filename, "experiment_id": e.id, "eid": e.eid, "title": e.title,
                    "same_experiment": e.id == home, "created_at": a.created_at.isoformat() + "Z"})
    out.sort(key=lambda r: not r["same_experiment"])
    return out[:limit]
