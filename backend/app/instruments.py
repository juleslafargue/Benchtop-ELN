"""Instrument folders: files an instrument saves into a watched folder are attached to the right experiment.

How it works
  * An admin adds a folder (Admin → Instruments): a name, a type, and who sees its unmatched files.
  * About once a minute Benchtop lists each folder. It only reads: nothing in a watched folder is ever moved,
    renamed or deleted. Folders are polled rather than watched for change events, because events don't
    arrive reliably from network drives, OneDrive or Dropbox; that's also why the same code works unchanged
    on a lab server (mount the share there and give its path).
  * What was already in a folder when it was added is noted and left alone ("baseline"), whatever its dates:
    a USB copy or a sync keeps a file's original date, so dates can't tell new from old.
  * An arrival is one file, or one dataset folder (a Bruker experiment number). It's handled once nothing in
    it has changed for the folder's quiet time (2 minutes by default), so half-copied files aren't taken.
  * Its name, folder names and (for Bruker) its title are searched for an experiment number such as
    EXP-00042. Exactly one, for a draft: the files are attached to that experiment's "Instrument data"
    section. Anything else waits in the Inbox with a note saying why. Benchtop never guesses.

Visibility follows Benchtop's sharing rules (see `can_see`): an arrival's file names can be revealing, so the
Inbox shows each person only what they're allowed to see.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from sqlalchemy import update
from sqlmodel import Session, select

from . import audit
from .config import settings
from .models import (
    Attachment, Entry, EntryKind, EntryVersion, Experiment, ExperimentStatus, Instrument, InstrumentFile,
    Notification, Role, User, utcnow,
)

log = logging.getLogger("benchtop.instruments")

KINDS = {
    "bruker_nmr": "Bruker NMR",
    "files": "Any files",
}
SYSTEM_USERNAME = "@instruments"  # can't be a real username (no '@' allowed), so it never clashes
SECTION_TITLE = "Instrument data"
EID = re.compile(r"(?<![A-Za-z0-9])EXP-(\d{5,})(?!\d)", re.IGNORECASE)
MAX_DEPTH = 10
# never arrivals: system clutter, partial downloads and files still being written by common tools
_SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini", ".localized", "icon\r"}
_SKIP_SUFFIXES = (".tmp", ".temp", ".part", ".partial", ".crdownload", ".download", ".icloud", ".lock", ".lck")


# ====================================================================== looking at a folder

def _skip(name: str) -> bool:
    low = name.lower()
    return name.startswith(".") or name.startswith("~$") or low in _SKIP_NAMES or low.endswith(_SKIP_SUFFIXES)


def discover(root: Path, kind: str) -> Iterator[tuple[str, Path]]:
    """(path inside the folder, full path) for each arrival currently there. Never follows links out."""
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not _skip(d))
        if len(here.parts) - root_depth >= MAX_DEPTH:
            dirnames[:] = []
        if kind == "bruker_nmr":
            if "acqus" in filenames and here != root and not (here / "acqus").is_symlink():  # one experiment number: the whole folder is one arrival
                dirnames[:] = []
                yield here.relative_to(root).as_posix(), here
            continue
        for f in sorted(filenames):
            if not _skip(f):
                p = here / f
                if p.is_file() and not p.is_symlink():
                    yield p.relative_to(root).as_posix(), p


def _files_in(p: Path) -> list[Path]:
    """The files of an arrival. Links are never followed: someone who can write to an instrument share
    mustn't be able to make Benchtop copy a file from elsewhere on the computer (its secret key, say)."""
    if p.is_file():
        return [] if p.is_symlink() else [p]
    out = []
    for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for f in sorted(filenames):
            fp = Path(dirpath) / f
            if not f.startswith(".") and f.lower() not in _SKIP_NAMES and not fp.is_symlink() and fp.is_file():
                out.append(fp)
    return out


def fingerprint(p: Path) -> tuple[str, int, float]:
    """(fingerprint, total size, newest modification time) of a file or a folder's contents."""
    h = hashlib.sha1()
    total, newest = 0, 0.0
    for f in _files_in(p):
        st = f.stat()
        total += st.st_size
        newest = max(newest, st.st_mtime)
        h.update(f"{f.relative_to(p) if f != p else f.name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest(), total, newest


def describe_text(p: Path, kind: str) -> str:
    """Extra text that may name the experiment: for Bruker, the title typed in TopSpin or IconNMR."""
    if kind == "bruker_nmr" and p.is_dir():
        for t in (p / "pdata" / "1" / "title", p / "title"):
            try:
                return t.read_bytes()[:4096].decode("latin-1")
            except OSError:
                continue
    return ""


# ====================================================================== storing what arrived

def _incoming() -> Path:
    d = settings.data_dir / ".incoming"  # outside the attachments folder, so a backup never picks up half a file
    d.mkdir(parents=True, exist_ok=True)
    return d


def blob_path(sha: str) -> Path:
    return settings.attachments_dir / sha[:2] / sha


def _store(tmp: Path) -> tuple[str, int]:
    """Move a finished temporary file into the content-addressed attachment store."""
    h = hashlib.sha256()
    with open(tmp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    sha, size = h.hexdigest(), tmp.stat().st_size
    dst = blob_path(sha)
    if dst.exists():
        tmp.unlink()
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, dst)
    return sha, size


def _tmp_name() -> Path:
    return _incoming() / f"{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}.part"


def pack(rel: str, p: Path, kind: str) -> list[dict[str, Any]]:
    """Copy an arrival into Benchtop's store: a file as it is, a dataset folder as one zip. A Bruker experiment
    number goes in as <sample>/<expno>/…, which the spectrum viewer opens directly."""
    tmp = _tmp_name()
    try:
        if p.is_file():
            with open(p, "rb") as fi, open(tmp, "wb") as fo:
                shutil.copyfileobj(fi, fo, 1 << 20)
            name = p.name
        else:
            parent = Path(rel).parent.name
            name = f"{parent}_{p.name}.zip" if parent else f"{p.name}.zip"
            top = Path(parent) / p.name if parent else Path(p.name)
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for f in _files_in(p):
                    z.write(f, (top / f.relative_to(p)).as_posix())
        sha, size = _store(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    ctype = "application/zip" if name.endswith(".zip") else (mimetypes.guess_type(name)[0] or "application/octet-stream")
    return [{"name": name, "sha256": sha, "size": size, "content_type": ctype}]


# ====================================================================== deciding where it goes

@dataclass
class Decision:
    action: str  # attach | inbox
    note: str = ""
    experiment: Optional[Experiment] = None
    matched_ids: list[int] = field(default_factory=list)
    rule: str = ""


def experiment_numbers(*texts: str) -> list[int]:
    seen: list[int] = []
    for t in texts:
        for m in EID.finditer(t or ""):
            n = int(m.group(1))
            if n not in seen:
                seen.append(n)
    return seen


def decide(session: Session, rel: str, extra: str = "") -> Decision:
    """Where an arrival goes. The rules, in order; the first that fits wins, and anything unclear waits:
      1. exactly one experiment number (EXP-00042) in its name, folder names or title, for a draft → attach;
      2. otherwise → Inbox, with a note saying why."""
    nums = experiment_numbers(rel, extra)
    if len(nums) > 1:
        found = [e.id for e in (session.get(Experiment, n) for n in nums) if e and not e.is_template]
        names = " and ".join(f"EXP-{n:05d}" for n in nums[:4])
        return Decision("inbox", f"The name mentions more than one experiment ({names}), so Benchtop didn't choose.",
                        matched_ids=found)
    if len(nums) == 1:
        exp = session.get(Experiment, nums[0])
        eid = f"EXP-{nums[0]:05d}"
        if exp is None or exp.is_template:
            return Decision("inbox", f"There's no experiment {eid}. Check the number, or assign it to the right one.")
        if exp.status == ExperimentStatus.open:
            return Decision("attach", experiment=exp, matched_ids=[exp.id], rule="eid")
        if exp.status == ExperimentStatus.voided:
            return Decision("inbox", f"{eid} was voided, so nothing more is added to it.", matched_ids=[exp.id])
        return Decision("inbox", f"Matched {eid}, but it's already signed, so it wasn't attached. "
                                 "Attach it to a follow-up experiment.", matched_ids=[exp.id])
    return Decision("inbox", "No experiment number (like EXP-00042) in the name.")


# ====================================================================== attaching

def system_user(session: Session) -> User:
    """The account instrument files arrive under. It can't sign in, and isn't listed with people."""
    u = session.exec(select(User).where(User.username == SYSTEM_USERNAME)).first()
    if u is None:
        u = User(username=SYSTEM_USERNAME, full_name="Instruments", role=Role.viewer, active=False,
                 password_hash="!disabled")
        session.add(u)
        session.flush()
    return u


class NotOpen(Exception):
    """The experiment was signed (or voided) before the files could be attached."""


def _claim_open(session: Session, exp: Experiment) -> None:
    res = session.execute(update(Experiment).where(Experiment.id == exp.id, Experiment.status == ExperimentStatus.open)
                          .values(status=ExperimentStatus.open).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise NotOpen(exp.eid)


def instrument_section(session: Session, exp: Experiment, actor: User) -> Entry:
    """The experiment's "Instrument data" section, made at the end if it has none yet."""
    for e in session.exec(select(Entry).where(Entry.experiment_id == exp.id, Entry.deleted == False,  # noqa: E712
                                              Entry.kind == EntryKind.file).order_by(Entry.position, Entry.id)):
        if (e.content or {}).get("source") == "instruments":
            return e
    last = session.exec(select(Entry.position).where(Entry.experiment_id == exp.id).order_by(Entry.position.desc())).first()
    content = {"attachment_ids": [], "caption": "", "source": "instruments"}
    e = Entry(experiment_id=exp.id, kind=EntryKind.file, title=SECTION_TITLE, content=content,
              position=(last if last is not None else -1) + 1, updated_by=actor.id)
    session.add(e)
    session.flush()
    session.add(EntryVersion(entry_id=e.id, version=1, title=e.title, content=content, created_by=actor.id))
    audit.record(session, actor, "create", "entry", e.id, experiment=exp.eid, kind="file", title=e.title)
    return e


def _set_section_ids(session: Session, e: Entry, ids: list[int], actor: User, exp: Experiment) -> None:
    content = {**(e.content or {}), "attachment_ids": ids}
    e.content = content
    e.version += 1
    e.updated_by, e.updated_at = actor.id, utcnow()
    session.add_all([e, EntryVersion(entry_id=e.id, version=e.version, title=e.title, content=content, created_by=actor.id)])
    audit.record(session, actor, "update", "entry", e.id, experiment=exp.eid, version=e.version,
                 content_sha256=hashlib.sha256(audit.canonical(content).encode()).hexdigest())


def attach(session: Session, row: InstrumentFile, inst: Instrument, exp: Experiment, actor: User, rule: str) -> list[int]:
    """Attach an arrival's files to a draft experiment. Raises NotOpen if it's no longer a draft.
    The caller commits; everything here is one transaction with its audit events."""
    audit._take_write_lock(session)
    _claim_open(session, exp)
    session.refresh(exp)
    ids = []
    for f in row.files:
        a = Attachment(experiment_id=exp.id, filename=f["name"], content_type=f["content_type"], size=f["size"],
                       sha256=f["sha256"], uploaded_by=actor.id, instrument_file_id=row.id)
        session.add(a)
        session.flush()
        audit.record(session, actor, "upload", "attachment", a.id, experiment=exp.eid, filename=a.filename,
                     sha256=a.sha256, size=a.size, instrument=inst.name, path=row.path)
        ids.append(a.id)
    sec = instrument_section(session, exp, actor)
    _set_section_ids(session, sec, [*(sec.content or {}).get("attachment_ids", []), *ids], actor, exp)
    exp.updated_at = utcnow()
    session.add(exp)
    row.state, row.experiment_id, row.attachment_ids, row.rule = "attached", exp.id, ids, rule
    row.handled_at = utcnow()
    session.add(row)
    audit.record(session, actor, "instrument_attach", "experiment", exp.id, experiment=exp.eid, instrument=inst.name,
                 path=row.path, revision=row.revision, rule=rule, files=[f["sha256"] for f in row.files])
    return ids


def notify(session: Session, user_id: int, text: str, experiment_id: Optional[int] = None, kind: str = "instrument") -> None:
    session.add(Notification(user_id=user_id, kind=kind, actor_id=system_user(session).id,
                             experiment_id=experiment_id, text=text))


# ====================================================================== who sees an Inbox item

def can_see(session: Session, user: User, row: InstrumentFile, inst: Instrument) -> bool:
    """* carries someone's sample code → that person (and admins);
       * names experiments (signed, voided, or more than one) → people who can read one of them;
       * matches nothing → the folder's audience: everyone, or members of its groups."""
    if user.role == Role.admin:
        return True
    from .access import access_for
    acc = access_for(session, user)
    if row.person_id is not None:
        return row.person_id == user.id
    if row.matched_ids:
        return any(acc.can_read(e) for e in (session.get(Experiment, i) for i in row.matched_ids) if e)
    if not inst.audience:
        return True
    return any(g in acc.groups for g in inst.audience)


# ====================================================================== the scan

def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _handle(session: Session, row: InstrumentFile, inst: Instrument, p: Path) -> None:
    """A settled arrival: store it, decide, attach or send to the Inbox. Reading the files comes first, so an
    unreadable arrival (a sync still downloading it) raises OSError before anything is written."""
    max_bytes = settings.instrument_max_mb * 1024 * 1024
    if row.size > max_bytes:
        row.state, row.files = "inbox", []
        row.note = (f"It's {row.size / 1e6:,.0f} MB, more than the {settings.instrument_max_mb:,} MB limit, so it wasn't "
                    "copied into Benchtop. Raise BENCHTOP_INSTRUMENT_MAX_MB, or keep it on the instrument drive.")
        session.add(row)
        return
    files = pack(row.path, p, inst.kind)
    extra = describe_text(p, inst.kind)
    row.files = files
    d = decide(session, row.path, extra)
    row.matched_ids = d.matched_ids
    if d.action == "attach" and d.experiment is not None:
        try:
            attach(session, row, inst, d.experiment, system_user(session), d.rule)
            n = len(row.files)
            notify(session, d.experiment.owner_id, f"attached {n} file{'s' if n != 1 else ''} from {inst.name} "
                   f"({row.path}) to {d.experiment.eid}", d.experiment.id)
            return
        except NotOpen:
            d = Decision("inbox", f"Matched {d.experiment.eid}, but it was signed just before the files arrived, so "
                                  "they weren't attached. Attach them to a follow-up experiment.", matched_ids=[d.experiment.id])
            row.matched_ids = d.matched_ids
    row.state, row.note = "inbox", d.note
    session.add(row)
    if len(d.matched_ids) == 1:  # the record's owner hears about it; unmatched files just wait in the Inbox
        exp = session.get(Experiment, d.matched_ids[0])
        if exp:
            notify(session, exp.owner_id, f"has a file from {inst.name} ({row.path}) for {exp.eid} waiting in your Inbox",
                   kind="inbox")


def scan(session: Session, inst: Instrument, now: Optional[datetime] = None) -> dict[str, int]:
    """Look at one folder once. Returns counts of what happened."""
    now = now or utcnow()
    root = Path(inst.folder)
    counts = {"seen": 0, "new": 0, "handled": 0, "errors": 0}
    if not root.is_dir():
        raise FileNotFoundError(f"{root} isn't there. If it's a network or external drive, check it's connected.")
    first = inst.baseline_at is None  # the first look: whatever is there already is noted, not taken
    quiet = timedelta(seconds=inst.settle_seconds)
    latest: dict[str, InstrumentFile] = {}
    for r in session.exec(select(InstrumentFile).where(InstrumentFile.instrument_id == inst.id)
                          .order_by(InstrumentFile.revision)):
        latest[r.path] = r
    for rel, p in discover(root, inst.kind):
        counts["seen"] += 1
        try:
            fp, size, newest = fingerprint(p)
        except OSError:
            counts["errors"] += 1
            continue
        row = latest.get(rel)
        if row is None:
            row = InstrumentFile(instrument_id=inst.id, path=rel, fingerprint=fp, size=size, first_seen_at=now,
                                 changed_at=now, state="baseline" if first else "settling")
            session.add(row)
            session.flush()
            latest[rel] = row
            if not first:
                counts["new"] += 1
        if row.state == "baseline":
            if inst.import_since and _utc(newest) >= inst.import_since:
                # older data the admin asked for: already quiet, so it's taken now
                row.state, row.fingerprint, row.size, row.changed_at = "settling", fp, size, now - quiet
                counts["new"] += 1
            elif row.fingerprint != fp:  # something already there changed after Benchtop noted it: that's new data
                row.state, row.fingerprint, row.size, row.changed_at = "settling", fp, size, now
                counts["new"] += 1
                continue
            else:
                continue
        elif row.fingerprint != fp:
            if row.state == "settling":  # still being written or copied
                row.fingerprint, row.size, row.changed_at = fp, size, now
                session.add(row)
            else:  # it changed after being handled: the change arrives again, as the next revision
                nxt = InstrumentFile(instrument_id=inst.id, path=rel, revision=row.revision + 1, fingerprint=fp,
                                     size=size, first_seen_at=now, changed_at=now, state="settling")
                session.add(nxt)
                session.flush()
                latest[rel] = nxt
                counts["new"] += 1
            continue
        if row.state == "settling" and now - row.changed_at >= quiet:
            try:
                _handle(session, row, inst, p)
                counts["handled"] += 1
            except OSError as exc:  # unreadable for now (a sync still downloading it, permissions): try next time
                log.warning("Couldn't read %s in %s: %s", rel, inst.name, exc)
                counts["errors"] += 1
    if first:
        inst.baseline_at = now
        session.add(inst)
    return counts


_scan_lock = threading.Lock()


def scan_all(now: Optional[datetime] = None) -> dict[int, dict[str, Any]]:
    """Look at every enabled folder once. Each folder is its own transaction, so one broken folder doesn't
    hold up the others; its problem shows on the Admin page."""
    from .db import engine
    out: dict[int, dict[str, Any]] = {}
    with _scan_lock:
        with Session(engine) as s:
            ids = [i.id for i in s.exec(select(Instrument).where(Instrument.enabled == True,  # noqa: E712
                                                                 Instrument.archived == False))]  # noqa: E712
        for iid in ids:
            with Session(engine) as s:
                inst = s.get(Instrument, iid)
                t0 = time.monotonic()
                try:
                    res: dict[str, Any] = scan(s, inst, now)
                    inst.last_error = ""
                except Exception as exc:  # noqa: BLE001 - reported on the Admin page, never stops the others
                    s.rollback()
                    inst = s.get(Instrument, iid)
                    res = {"error": folder_problem(exc)}
                    inst.last_error = res["error"]
                inst.last_scan_at = now or utcnow()
                inst.last_scan_ms = int((time.monotonic() - t0) * 1000)
                s.add(inst)
                s.commit()
                out[iid] = res
    return out


def folder_problem(exc: BaseException) -> str:
    """A folder error in words that say what to do."""
    if isinstance(exc, PermissionError):
        return ("Benchtop isn't allowed to read this folder. On a Mac: System Settings → Privacy & Security → "
                "Files and Folders (or Full Disk Access), allow Terminal, then restart Benchtop.")
    if isinstance(exc, FileNotFoundError):
        return str(exc) if "isn't there" in str(exc) else "The folder isn't there. If it's on a network or external drive, check it's connected."
    return f"Couldn't read the folder: {exc}"


# ====================================================================== checking a folder before using it

def validate_folder(raw: str) -> Path:
    raw = (raw or "").strip()
    if not raw or len(raw) > 1024:
        raise ValueError("Give the full path of the folder, e.g. /Volumes/NMR/data/ruizlab")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ValueError("Give the full path of the folder, starting with / (e.g. /Volumes/NMR/data/ruizlab)")
    p = p.resolve()
    data = settings.data_dir.resolve()
    if p == data or data in p.parents or p in data.parents:
        raise ValueError("Choose a folder outside Benchtop's own data folder")
    try:
        if not p.is_dir():
            raise ValueError(f"{p} isn't there. If it's on a network or external drive, check it's connected.")
        next(iter(os.scandir(p)), None)
    except PermissionError as exc:
        raise ValueError(folder_problem(exc)) from None
    return p


def preview(session: Session, folder: str, kind: str, import_since: Optional[datetime] = None, limit: int = 12) -> dict[str, Any]:
    """What Benchtop would do with a folder, without doing any of it."""
    root = validate_folder(folder)
    t0 = time.monotonic()
    items = []
    for rel, p in discover(root, kind):
        try:
            _, size, newest = fingerprint(p)
        except OSError:
            continue
        items.append((newest, rel, p, size))
        if len(items) >= 50_000:
            break
    seconds = time.monotonic() - t0
    items.sort(reverse=True)
    def outcome(rel: str, p: Path) -> dict[str, Any]:
        d = decide(session, rel, describe_text(p, kind))
        return {"path": rel, "action": d.action, "note": d.note,
                "experiment": d.experiment.eid if d.experiment else None}
    latest = [{**outcome(rel, p), "size": size, "modified": _utc(t).isoformat() + "Z"}
              for t, rel, p, size in items[:limit]]
    out: dict[str, Any] = {"folder": str(root), "items": len(items), "scan_seconds": round(seconds, 2), "latest": latest,
                           "hints": _hints(root, seconds)}
    if import_since:
        old = [(rel, p) for t, rel, p, _ in items if _utc(t) >= import_since]
        res = [outcome(rel, p) for rel, p in old[:5000]]
        out["since"] = {"items": len(old), "attach": sum(r["action"] == "attach" for r in res),
                        "inbox": sum(r["action"] == "inbox" for r in res)}
    return out


def _hints(root: Path, seconds: float) -> list[str]:
    hints = []
    s = str(root)
    if "/Library/CloudStorage/OneDrive" in s or "/OneDrive" in s:
        hints.append("OneDrive folder: in Finder, right-click it and choose “Always Keep on This Device”, "
                     "so files are really on this Mac when Benchtop reads them.")
    if "/Library/Mobile Documents/" in s:
        hints.append("iCloud Drive folder: turn off “Optimise Mac Storage” for it, or files may not be on this Mac.")
    if seconds > 20:
        hints.append(f"Looking through this folder took {seconds:.0f} s. Choose a smaller folder (for example your "
                     "group's own subfolder) so new files are noticed quickly.")
    return hints


# ====================================================================== running in the background

_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def _loop() -> None:
    while not _stop.is_set():
        try:
            scan_all()
        except Exception:  # noqa: BLE001 - keep watching
            log.exception("Instrument folder scan failed")
        _stop.wait(max(5, settings.instrument_scan_seconds))


def start() -> None:
    global _thread
    if settings.instrument_scan_seconds <= 0 or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="benchtop-instruments", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
