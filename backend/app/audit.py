"""Append-only, hash-chained audit trail.

Every event stores SHA-256(prev_hash || canonical_json(event)). Altering, deleting or
reordering any historic row breaks the chain, which `verify_chain` detects. This gives
tamper *evidence* in the spirit of 21 CFR Part 11 §11.10(e); it is not, by itself, a
validated compliant system.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from .models import AuditEvent, User, utcnow

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    def default(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "value"):
            return o.value
        raise TypeError(type(o))

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=default)


def _event_payload(ev: AuditEvent) -> dict[str, Any]:
    return {
        "ts": ev.ts.isoformat(),
        "user_id": ev.user_id,
        "username": ev.username,
        "action": ev.action,
        "object_type": ev.object_type,
        "object_id": ev.object_id,
        "details": ev.details,
    }


def compute_hash(prev_hash: str, ev: AuditEvent) -> str:
    return hashlib.sha256((prev_hash + canonical(_event_payload(ev))).encode()).hexdigest()


def record(
    session: Session,
    user: User | None,
    action: str,
    object_type: str,
    object_id: int | None,
    **details: Any,
) -> AuditEvent:
    """Append an event. Caller commits (so the audit row is atomic with the change)."""
    last = session.exec(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).first()
    prev = last.hash if last else GENESIS
    ev = AuditEvent(
        ts=utcnow(),
        user_id=user.id if user else None,
        username=user.username if user else "system",
        action=action,
        object_type=object_type,
        object_id=object_id,
        details=json.loads(canonical(details)),
        prev_hash=prev,
    )
    ev.hash = compute_hash(prev, ev)
    session.add(ev)
    from . import webhooks  # sent after the commit, if any webhook listens for this
    webhooks.note(session, user, action, object_type, object_id, details)
    return ev


def verify_chain(session: Session) -> dict[str, Any]:
    prev = GENESIS
    count = 0
    for ev in session.exec(select(AuditEvent).order_by(AuditEvent.id)):
        count += 1
        if ev.prev_hash != prev:
            return {"ok": False, "checked": count, "broken_at": ev.id, "reason": "prev_hash mismatch"}
        if compute_hash(prev, ev) != ev.hash:
            return {"ok": False, "checked": count, "broken_at": ev.id, "reason": "hash mismatch"}
        prev = ev.hash
    return {"ok": True, "checked": count, "head": prev}
