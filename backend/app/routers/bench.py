"""Everyday bench helpers: the timestamped bench log, quick links between records, your usual
reagents, and handing an experiment over to your phone with a QR code."""
from __future__ import annotations

import html
import re
import secrets
import threading
import time
from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from .. import audit
from ..config import settings
from ..models import Attachment, Entry, EntryKind, EntryVersion, Experiment, Material, User, utcnow
from ..security import DB, CurrentUser, Writer, start_session
from ..services import entry_out, live_entries, read_experiment, require_editable

router = APIRouter(prefix="/api", tags=["bench"])
LOG_TITLE = "Bench log"


# ====================================================================== bench log

class LogIn(BaseModel):
    text: str = ""
    time: str = ""   # the author's local clock, e.g. "14:32" (the server may be in another time zone)
    day: str = ""    # e.g. "Sat 17 Oct 2026"; a heading is added when the day changes
    attachment_id: int | None = None


def _log_entry(session, exp: Experiment, user: User) -> Entry:
    entries = live_entries(session, exp.id)
    e = next((x for x in entries if x.kind == EntryKind.text and (x.content or {}).get("bench_log")), None) \
        or next((x for x in entries if x.kind == EntryKind.text and x.title.strip().lower() == LOG_TITLE.lower()), None)
    if e:
        return e
    # a new log goes just before the results section if there is one, else at the end
    results = next((x for x in entries if x.kind == EntryKind.text and re.match(r"(?i)observations|results", x.title)), None)
    pos = results.position if results else (max((x.position for x in entries), default=-1) + 1)
    for x in entries:
        if x.position >= pos:
            x.position += 1
            session.add(x)
    e = Entry(experiment_id=exp.id, kind=EntryKind.text, title=LOG_TITLE, position=pos, content={"html": "", "bench_log": True},
              updated_by=user.id)
    session.add(e)
    session.flush()
    session.add(EntryVersion(entry_id=e.id, version=1, title=e.title, content=e.content, created_by=user.id))
    audit.record(session, user, "create", "entry", e.id, experiment=exp.eid, kind="text", title=e.title)
    return e


@router.post("/experiments/{exp_id}/log")
def add_log_line(exp_id: int, body: LogIn, user: Writer, session: DB):
    """Append one timestamped line (and optionally a photo) to the experiment's bench log."""
    from .experiments import clean_html
    exp = read_experiment(session, user, exp_id)
    require_editable(exp, user)
    text = body.text.strip()[:2000]
    att = session.get(Attachment, body.attachment_id) if body.attachment_id else None
    if att and att.experiment_id != exp.id:
        raise HTTPException(404, "No such file on this experiment")
    if not text and not att:
        raise HTTPException(422, "Write something to log")
    t = body.time if re.fullmatch(r"\d{1,2}:\d{2}", body.time or "") else utcnow().strftime("%H:%M UTC")
    with _log_lock:
        e = _log_entry(session, exp, user)
        c = dict(e.content or {})
        add = ""
        day = html.escape(body.day.strip()[:40])
        if day and c.get("log_day") != day:
            add += f"<h3>{day}</h3>"
            c["log_day"] = day
        line = f"<strong>{t}</strong>" + (f" {html.escape(text)}" if text else "")
        if att:
            line += f" <em>({html.escape(att.filename)})</em>"
        add += f"<p>{line}</p>"
        if att and att.content_type.startswith("image/"):
            add += f'<p><img data-attachment-id="{att.id}" alt="{html.escape(att.filename)}"></p>'
        c["html"] = clean_html((c.get("html") or "") + add)
        c["bench_log"] = True
        e.content = c
        e.version += 1
        e.updated_by, e.updated_at = user.id, utcnow()
        exp.updated_at = e.updated_at
        session.add_all([e, exp, EntryVersion(entry_id=e.id, version=e.version, title=e.title, content=c, created_by=user.id)])
        audit.record(session, user, "update", "entry", e.id, experiment=exp.eid, version=e.version, bench_log=True)
        session.commit()
    return entry_out(e)


_log_lock = threading.Lock()


# ====================================================================== EXP-/MAT- links

@router.get("/lookup/{code}")
def lookup(code: str, user: CurrentUser, session: DB):
    """What an EXP-00025 or MAT-00007 mention points to (only if you're allowed to see it)."""
    code = code.strip().upper()
    if re.fullmatch(r"EXP-\d{1,8}", code):
        from ..access import visible_experiment_filter
        e = session.exec(visible_experiment_filter(select(Experiment).where(Experiment.id == int(code[4:]), Experiment.is_template == False),  # noqa: E712
                                                   session, user)).first()
        if not e:
            raise HTTPException(404, f"{code} doesn't exist or isn't shared with you")
        owner = session.get(User, e.owner_id)
        smiles = ""
        for en in live_entries(session, e.id):
            if en.kind == EntryKind.reaction and (en.content or {}).get("reaction_smiles"):
                smiles = en.content["reaction_smiles"]
                break
        return {"kind": "experiment", "id": e.id, "code": e.eid, "title": e.title, "status": e.status.value,
                "author": owner.full_name or owner.username if owner else "", "date": e.created_at.isoformat() + "Z", "smiles": smiles}
    if re.fullmatch(r"MAT-\d{1,8}", code):
        m = session.get(Material, int(code[4:]))
        if not m:
            raise HTTPException(404, f"{code} doesn't exist")
        return {"kind": "material", "id": m.id, "code": m.mid, "title": m.name, "formula": m.formula, "mw": m.mw, "cas": m.cas, "smiles": m.smiles}
    raise HTTPException(404, "Not a record number")


# ====================================================================== your usual reagents

@router.get("/frequent-materials")
def frequent_materials(user: CurrentUser, session: DB, limit: int = 12):
    """The registry materials you add to reactions most, with the role and lot you used last."""
    exps = session.exec(select(Experiment.id).where(Experiment.owner_id == user.id)).all()
    if not exps:
        return []
    counts: Counter[int] = Counter()
    roles: dict[int, Counter[str]] = {}
    last: dict[int, tuple[Any, int | None]] = {}
    rows = session.exec(select(Entry).where(Entry.experiment_id.in_(exps), Entry.kind == EntryKind.reaction, Entry.deleted == False)  # noqa: E712
                        .order_by(Entry.updated_at.desc()).limit(400)).all()
    for en in rows:
        for r in (en.content or {}).get("rows") or []:
            mid = r.get("material_id")
            if not isinstance(mid, int) or r.get("role") == "product":
                continue
            counts[mid] += 1
            roles.setdefault(mid, Counter())[r.get("role") or "reagent"] += 1
            if mid not in last:
                last[mid] = (en.updated_at, r.get("lot_id"))
    out = []
    for mid, n in counts.most_common(limit):
        m = session.get(Material, mid)
        if not m:
            continue
        out.append({"id": m.id, "mid": m.mid, "name": m.name, "formula": m.formula, "mw": m.mw, "smiles": m.smiles, "kind": m.kind,
                    "uses": n, "role": roles[mid].most_common(1)[0][0], "last_lot_id": last[mid][1],
                    "last_used": last[mid][0].isoformat() + "Z"})
    return out


# ====================================================================== phone at the bench

_handoffs: dict[str, tuple[int, int, float]] = {}  # code -> (user id, experiment id, expires)
_hlock = threading.Lock()
HANDOFF_SECONDS = 300


def _base_url(request: Request) -> tuple[str, bool, str]:
    """(address a phone should open, whether a phone can probably reach it, advice if not)."""
    if settings.public_url:
        return settings.public_url, True, ""
    if settings.lan_url:
        return settings.lan_url, True, "Your phone must be on the same Wi-Fi network as this computer."
    host = request.url.hostname or ""
    base = str(request.base_url).rstrip("/")
    if host in ("localhost", "127.0.0.1", "::1"):
        return base, False, ("Benchtop is running just for this computer, so your phone can't reach it. "
                             "Start it with ./benchtop lab instead (it shares Benchtop on your Wi-Fi), then try again.")
    return base, True, "Your phone must be able to reach this address (same network, or the lab's server)."


@router.post("/experiments/{exp_id}/phone-link")
def phone_link(exp_id: int, request: Request, user: CurrentUser, session: DB):
    """A QR code that opens this experiment's bench view on a phone, already signed in as you
    (valid for 5 minutes, once)."""
    import segno
    exp = read_experiment(session, user, exp_id)
    if getattr(user, "_token", None):
        raise HTTPException(403, "API tokens can't hand over a session")
    base, reachable, hint = _base_url(request)
    code = secrets.token_urlsafe(24)
    now = time.time()
    with _hlock:
        for k, v in list(_handoffs.items()):
            if v[2] < now:
                del _handoffs[k]
        _handoffs[code] = (user.id, exp.id, now + HANDOFF_SECONDS)
    url = f"{base}/bench/{exp.id}#handoff={code}"
    svg = segno.make(url, error="m").svg_inline(scale=5, dark="#111", light="#fff", border=2)
    return {"url": url, "svg": svg, "reachable": reachable, "hint": hint, "expires_in": HANDOFF_SECONDS}


class RedeemIn(BaseModel):
    code: str


@router.post("/auth/handoff")
def redeem_handoff(body: RedeemIn, request: Request, session: DB):
    with _hlock:
        item = _handoffs.pop(body.code, None)
    if not item or item[2] < time.time():
        raise HTTPException(401, "This QR code has expired or was already used. Make a new one on the computer.")
    user = session.get(User, item[0])
    if not user or not user.active:
        raise HTTPException(401, "This account can't sign in")
    from .auth import user_out
    token = start_session(session, user, request, hours=12, method="phone")
    audit.record(session, user, "login", "user", user.id, method="phone", experiment_id=item[1])
    session.commit()
    return {"token": token, "user": user_out(user), "experiment_id": item[1]}
