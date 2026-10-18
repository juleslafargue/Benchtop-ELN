"""Outgoing webhooks: Benchtop calls other services when something happens.

Events are collected while a request runs (from the audit log) and sent only after its database
transaction commits, so a webhook never announces a change that was rolled back. Delivery happens on
a background thread with a few retries; each request is signed with the webhook's secret
(X-Benchtop-Signature: sha256=<HMAC of the body>) so the receiver can check it came from Benchtop.

Payloads carry identifiers, titles and who did what, never the content of a record.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import time
from typing import Any

import httpx
from sqlalchemy import event as sa_event
from sqlmodel import Session, select

from . import __version__
from .config import settings
from .models import Experiment, Notebook, User, Webhook, WebhookDelivery, utcnow

log = logging.getLogger("benchtop.webhooks")

EVENTS = {
    "experiment.created": "An experiment is created (or cloned)",
    "experiment.signed": "The author signs an experiment",
    "experiment.witnessed": "An experiment is witnessed",
    "experiment.returned": "A witness returns an experiment to its author",
    "experiment.withdrawn": "An author withdraws their signature",
    "experiment.voided": "An experiment is voided",
    "comment.created": "Someone comments on an experiment",
    "material.created": "A material is registered",
}
_ACTIONS = {
    ("create", "experiment"): "experiment.created", ("clone", "experiment"): "experiment.created",
    ("sign:author", "experiment"): "experiment.signed", ("sign:witness", "experiment"): "experiment.witnessed",
    ("sign:rejected", "experiment"): "experiment.returned", ("sign:withdrawn", "experiment"): "experiment.withdrawn",
    ("sign:void", "experiment"): "experiment.voided", ("comment", "experiment"): "comment.created",
    ("create", "material"): "material.created",
}
VERBS = {
    "experiment.created": "created", "experiment.signed": "signed", "experiment.witnessed": "witnessed",
    "experiment.returned": "returned to its author", "experiment.withdrawn": "withdrew the signature on",
    "experiment.voided": "voided", "comment.created": "commented on", "material.created": "registered",
}

_KEY = "benchtop_events"
_q: "queue.Queue[tuple[int, dict]]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()
# tests set this to deliver inline; a callable(url, body, headers) -> status code may replace HTTP
synchronous = False
sender = None


def note(session: Session, user: User | None, action: str, object_type: str, object_id: int | None, details: dict) -> None:
    """Called by audit.record: remember the event until the transaction commits."""
    ev = _ACTIONS.get((action, object_type))
    if ev and object_id is not None:
        session.info.setdefault(_KEY, []).append((ev, object_type, object_id, user.id if user else None, dict(details)))


@sa_event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    pending = session.info.pop(_KEY, None)
    if pending:
        for item in pending:
            _dispatch(*item)


@sa_event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_KEY, None)


def _url(path: str) -> str:
    return (settings.public_url or f"http://localhost:{settings.port}") + path


def build_payload(s: Session, ev: str, object_type: str, object_id: int, user_id: int | None, details: dict) -> tuple[dict, bool]:
    """(payload, is_private): what a receiver gets for one event."""
    actor = s.get(User, user_id) if user_id else None
    p: dict[str, Any] = {
        "event": ev, "timestamp": utcnow().isoformat() + "Z", "benchtop": __version__,
        "actor": {"username": actor.username, "name": actor.full_name or actor.username} if actor else None,
    }
    private = False
    if object_type == "experiment":
        exp = s.get(Experiment, object_id)
        if exp is None:
            return p, True
        nb = s.get(Notebook, exp.notebook_id) if exp.notebook_id else None
        owner = s.get(User, exp.owner_id)
        private = bool(nb and nb.visibility == "private")
        p["experiment"] = {
            "id": exp.id, "eid": exp.eid, "title": exp.title, "status": exp.status.value,
            "author": owner.full_name or owner.username if owner else None,
            "notebook": nb.name if nb else None, "url": _url(f"/experiments/{exp.id}"),
        }
        if ev == "comment.created":
            p["comment_id"] = details.get("comment_id")
        if details.get("comment") and ev in ("experiment.returned", "experiment.voided"):
            p["reason"] = str(details["comment"])[:500]
    elif object_type == "material":
        from .models import Material
        m = s.get(Material, object_id)
        if m:
            p["material"] = {"id": m.id, "mid": m.mid, "name": m.name, "url": _url(f"/materials/{m.id}")}
    return p, private


def summary(p: dict) -> str:
    who = (p.get("actor") or {}).get("name") or "Benchtop"
    verb = VERBS.get(p["event"], p["event"])
    if "experiment" in p:
        e = p["experiment"]
        s = f"{who} {verb} {e['eid']}: {e['title']}"
        if p.get("reason"):
            s += f" (“{p['reason']}”)"
        return s
    if "material" in p:
        return f"{who} {verb} {p['material']['mid']}: {p['material']['name']}"
    return f"{who}: {p['event']}"


def body_for(hook: Webhook, p: dict) -> dict:
    link = (p.get("experiment") or p.get("material") or {}).get("url")
    if hook.format == "slack":  # Slack incoming webhooks (also Mattermost, Rocket.Chat, Discord's /slack endpoint)
        return {"text": summary(p) + (f" <{link}|Open>" if link else "")}
    if hook.format == "teams":  # Microsoft Teams workflow / incoming webhook: simple text card
        return {"text": summary(p) + (f" [Open]({link})" if link else "")}
    return p


def sign_body(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _dispatch(ev: str, object_type: str, object_id: int, user_id: int | None, details: dict) -> None:
    from .db import engine
    with Session(engine) as s:
        hooks = [h for h in s.exec(select(Webhook).where(Webhook.active == True)).all() if ev in (h.events or [])]  # noqa: E712
        if not hooks:
            return
        payload, private = build_payload(s, ev, object_type, object_id, user_id, details)
    for h in hooks:
        if private and not h.include_private:
            continue
        job = (h.id, payload)
        if synchronous:
            deliver(*job)
        else:
            _q.put(job)
            _ensure_worker()


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="benchtop-webhooks", daemon=True)
            _worker.start()


def _run() -> None:
    while True:
        hook_id, payload = _q.get()
        try:
            deliver(hook_id, payload)
        except Exception:  # never let one bad hook stop the others
            log.exception("Webhook delivery crashed")


def deliver(hook_id: int, payload: dict, retries: int = 3) -> WebhookDelivery:
    from .db import engine
    with Session(engine) as s:
        hook = s.get(Webhook, hook_id)
        if hook is None:
            raise ValueError("No such webhook")
        raw = json.dumps(body_for(hook, payload), separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "User-Agent": f"Benchtop/{__version__}",
                   "X-Benchtop-Event": payload["event"], "X-Benchtop-Delivery": str(int(time.time() * 1000))}
        if hook.secret:
            headers["X-Benchtop-Signature"] = sign_body(hook.secret, raw)
        d = WebhookDelivery(webhook_id=hook.id, event=payload["event"])
        t0 = time.perf_counter()
        for attempt in range(1, retries + 1):
            d.attempts = attempt
            try:
                code = sender(hook.url, raw, headers) if sender else httpx.post(hook.url, content=raw, headers=headers, timeout=10).status_code
                d.status_code, d.ok, d.error = code, 200 <= code < 300, "" if 200 <= code < 300 else f"HTTP {code}"
            except Exception as e:  # network trouble: name it plainly
                d.ok, d.error = False, f"{type(e).__name__}: {e}"[:300]
            if d.ok or (d.status_code and 400 <= d.status_code < 500 and d.status_code != 429):
                break  # success, or the receiver rejected it: retrying won't help
            if attempt < retries and not synchronous:
                time.sleep(2 ** attempt)
        d.duration_ms = int((time.perf_counter() - t0) * 1000)
        s.add(d)
        # keep the last 50 deliveries per webhook
        old = s.exec(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id)
                     .order_by(WebhookDelivery.id.desc()).offset(50)).all()
        for o in old:
            s.delete(o)
        s.commit()
        s.refresh(d)
        return d
