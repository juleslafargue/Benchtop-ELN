"""Outgoing email: witness requests, reminders and outcomes.

Configured in Admin → Email (or BENCHTOP_SMTP_* variables). Messages are queued and sent by a background
thread, so a slow mail server never holds up a request; failures are retried and shown in the admin's log.
Without an email server everything still works in the app (notifications), just without the emails.
"""
from __future__ import annotations

import html
import logging
import queue
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Callable

from sqlmodel import Session, select

from . import sitesettings
from .config import settings
from .models import EmailLog, User, utcnow

log = logging.getLogger("benchtop.mail")
synchronous = False                              # tests: send inline
transport: Callable[[EmailMessage, dict], None] | None = None  # tests: capture instead of SMTP
_q: "queue.Queue[int]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()
KEEP = 200
RETRIES = 3


def configured() -> bool:
    if settings.demo:  # never email the demo lab's fictional people
        return False
    cfg = sitesettings.get("email")
    return bool(cfg["host"] and cfg["from_address"])


def base_url() -> str:
    """The address people open Benchtop at, for links in emails."""
    cfg = sitesettings.get("email")
    return (cfg["base_url"] or settings.public_url or settings.lan_url or f"http://localhost:{settings.port}").rstrip("/")


def wants(user: User | None, topic: str) -> bool:
    """Does this person get email for this topic? ("witness" requests/reminders, "results" of their records)."""
    if not user or not user.active or not (user.email or "").strip():
        return False
    prefs = ((user.preferences or {}).get("email_notify") or {})
    return prefs.get(topic, True) is not False


# ------------------------------------------------------------------ messages

def _layout(title: str, lines: list[str], link: str | None, button: str = "Open in Benchtop") -> str:
    body = "".join(f'<p style="margin:0 0 12px">{line}</p>' for line in lines)
    btn = (f'<p style="margin:20px 0"><a href="{html.escape(link)}" style="background:#b8336a;color:#fff;text-decoration:none;'
           f'padding:9px 16px;border-radius:7px;font-weight:600;display:inline-block">{html.escape(button)}</a></p>') if link else ""
    return (f'<div style="font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#17212c;max-width:560px">'
            f'<p style="margin:0 0 16px;font-weight:700;font-size:17px">{html.escape(title)}</p>{body}{btn}'
            f'<p style="margin:24px 0 0;color:#6f7d8b;font-size:12.5px">Sent by Benchtop. You can change which emails you get in '
            f'Account settings.</p></div>')


def render(kind: str, **c: Any) -> tuple[str, str, str]:
    """(subject, plain text, html) for one kind of email."""
    e = lambda v: html.escape(str(v))  # noqa: E731
    rec = f"{c.get('eid', '')} {c.get('title', '')}".strip()
    link = c.get("link")
    if kind == "witness_request":
        subject = f"Please witness {c['eid']}: {c['title']}"
        lines = [f"{e(c['author'])} signed <b>{e(rec)}</b> and asked you to witness it.",
                 f"Please review it by {e(c['due'])}." if c.get("due") else ""]
    elif kind == "witness_reminder":
        subject = f"Reminder: {c['eid']} is waiting for your witness signature"
        lines = [f"<b>{e(rec)}</b> by {e(c['author'])} has been waiting for your witness signature for {e(c['waiting'])}.",
                 "It's overdue." if c.get("overdue") else ""]
    elif kind == "witnessed":
        subject = f"{c['eid']} was witnessed"
        lines = [f"{e(c['witness'])} witnessed <b>{e(rec)}</b>. The record is now final."]
    elif kind == "returned":
        subject = f"{c['eid']} was returned to you"
        lines = [f"{e(c['witness'])} returned <b>{e(rec)}</b> to you for changes.", f"Reason: “{e(c.get('reason', ''))}”"]
    elif kind == "test":
        subject = "Benchtop test email"
        lines = ["This is a test from Benchtop's email settings. If you can read this, email is working."]
        link = base_url()
    else:
        raise ValueError(kind)
    lines = [ln for ln in lines if ln]
    plain = "\n\n".join(html.unescape(ln.replace("<b>", "").replace("</b>", "")) for ln in lines)
    if link:
        plain += f"\n\nOpen it: {link}"
    plain += "\n\n—\nSent by Benchtop. You can change which emails you get in Account settings."
    return subject, plain, _layout(subject, lines, link)


# ------------------------------------------------------------------ sending

def queue_email(session: Session, to: str, kind: str, **ctx: Any) -> None:
    """Queue an email; it goes out after the current transaction commits (nothing is sent if it rolls back)."""
    if not configured() or not to:
        return
    subject, _, _ = render(kind, **ctx)
    row = EmailLog(to=to, subject=subject, kind=kind)
    session.add(row)
    session.flush()
    session.info.setdefault("benchtop_mail", []).append((row.id, kind, ctx))


def _build(to: str, kind: str, ctx: dict) -> EmailMessage:
    cfg = sitesettings.get("email")
    subject, plain, rich = render(kind, **ctx)
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = formataddr((cfg["from_name"] or "Benchtop", cfg["from_address"]))
    msg["Message-ID"] = make_msgid(domain=(cfg["from_address"].split("@")[-1] or "benchtop.local"))
    msg.set_content(plain)
    msg.add_alternative(rich, subtype="html")
    return msg


def tls_context() -> ssl.SSLContext:
    """Trusted certificates for the encrypted connection. Python from python.org on a Mac has none of its own
    until "Install Certificates.command" is run, so also load the certifi bundle (installed with httpx)."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - fall back to the system's certificates
        pass
    return ctx


def _smtp_send(msg: EmailMessage, cfg: dict) -> None:
    if transport is not None:
        transport(msg, cfg)
        return
    ctx = tls_context()
    if cfg["security"] == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(cfg["host"], cfg["port"] or 465, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"] or 587, timeout=20)
    with server:
        server.ehlo()
        if cfg["security"] == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if cfg["username"]:
            server.login(cfg["username"], cfg["password"])
        server.send_message(msg)


def _explain(exc: Exception) -> str:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return ("Couldn't confirm the email server's identity (its security certificate didn't check out). "
                "Check the server name; on a Mac with Python from python.org, running \"Install Certificates.command\" "
                "in Applications → Python also fixes this")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "The email server refused the username or password (for Gmail or Outlook, use an app password)"
    if isinstance(exc, (smtplib.SMTPConnectError, ConnectionRefusedError, TimeoutError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return f"Couldn't reach the email server: {exc}"
    return str(exc) or exc.__class__.__name__


def deliver(log_id: int, kind: str, ctx: dict) -> None:
    from .db import engine
    with Session(engine) as s:
        row = s.get(EmailLog, log_id)
        if row is None:
            return
        cfg = sitesettings.get("email")
        error = ""
        for attempt in range(RETRIES):
            try:
                _smtp_send(_build(row.to, kind, ctx), cfg)
                row.status, row.sent_at, row.error = "sent", utcnow(), ""
                break
            except Exception as exc:  # noqa: BLE001 - keep trying, then record why
                error = _explain(exc)
                if synchronous or isinstance(exc, smtplib.SMTPAuthenticationError):
                    break
                time.sleep(2 ** attempt * 5)
        else:
            row.status, row.error = "failed", error
        if row.status != "sent":
            row.status, row.error = "failed", error
            log.warning("Email to %s failed: %s", row.to, error)
        s.add(row)
        old = s.exec(select(EmailLog).order_by(EmailLog.id.desc()).offset(KEEP)).all()
        for o in old:
            s.delete(o)
        s.commit()


def _run() -> None:
    while True:
        item = _q.get()
        try:
            deliver(*item)
        except Exception:  # noqa: BLE001
            log.exception("Email worker crashed on one message")


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="benchtop-mail", daemon=True)
            _worker.start()


def flush_after_commit(session: Session) -> None:
    for item in session.info.pop("benchtop_mail", None) or []:
        if synchronous:
            deliver(*item)
        else:
            _q.put(item)  # type: ignore[arg-type]
            _ensure_worker()


def drop_on_rollback(session: Session) -> None:
    session.info.pop("benchtop_mail", None)


def send_test(to: str) -> str:
    """Send a test email now; returns "" or what went wrong."""
    if not configured():
        return "Fill in the email server and the From address first"
    try:
        _smtp_send(_build(to, "test", {}), sitesettings.get("email"))
        return ""
    except Exception as exc:  # noqa: BLE001
        return _explain(exc)


def _install_hooks() -> None:
    from sqlalchemy import event
    event.listen(Session, "after_commit", flush_after_commit)
    event.listen(Session, "after_soft_rollback", lambda s, prev: drop_on_rollback(s))


_install_hooks()
