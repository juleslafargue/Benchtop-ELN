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
    _take_write_lock(session)  # read the head and append as one step: no two writers chain onto the same event
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


def _take_write_lock(session: Session) -> None:
    """Hold the database's write lock until this transaction commits, before reading the chain's head.

    Without it, two requests whose first write is an audit event could both read the same head and both
    chain onto it (a fork). SQLite has one writer at a time: a write statement (even one that changes no
    rows) takes that lock for the rest of the transaction. Postgres: a transaction-scoped advisory lock."""
    from sqlalchemy import text
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        session.execute(text("UPDATE auditevent SET id = id WHERE 0"))
    elif dialect == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(72716)"))


FORK_WINDOW = 64  # how far back a simultaneous write can have looked for "the latest event"


def verify_chain(session: Session) -> dict[str, Any]:
    """Every event must be intact (its hash matches its content) and chained onto an existing earlier event.

    Normally that is the event just before it. Versions before 0.13.7 could, under simultaneous writes,
    chain two events onto the same predecessor (a "fork"): both are intact and nothing is missing, so it is
    reported separately and doesn't count as a break. Editing, deleting or reordering events still breaks it."""
    from collections import deque
    prev = GENESIS
    recent: deque[str] = deque([GENESIS], maxlen=FORK_WINDOW)
    count = forks = 0
    for ev in session.exec(select(AuditEvent).order_by(AuditEvent.id)):
        count += 1
        if compute_hash(ev.prev_hash, ev) != ev.hash:
            return {"ok": False, "checked": count, "broken_at": ev.id, "reason": "hash mismatch"}
        if ev.prev_hash != prev:
            if ev.prev_hash not in recent:
                return {"ok": False, "checked": count, "broken_at": ev.id, "reason": "prev_hash mismatch"}
            forks += 1
        prev = ev.hash
        recent.append(ev.hash)
    return {"ok": True, "checked": count, "head": prev, "forks": forks}
