"""Health check of a Benchtop database: is it intact, and do the records still verify?

Used by `./benchtop check` (on a snapshot of the live data, safe while the server runs) and by the
weekly backup test (on a backup restored into a temporary folder).

CLI (run from backend/):
    python -m app.check                 # the live data
    python -m app.check backup.zip      # a backup, unpacked to a temporary folder
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlmodel import Session, select


def _item(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def check_files(db_path: Path, attachments_dir: Path) -> dict[str, Any]:
    """Every check on one database file plus its attachment folder (neither is modified)."""
    from . import audit, migrations
    from .models import Attachment, Experiment, ExperimentStatus, Signature
    from .services import content_hash

    checks: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    # 1. the file itself
    con = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        res = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall()]
    except sqlite3.DatabaseError as exc:
        con.close()
        return {"ok": False, "checks": [_item("Database file", False, f"Not a readable database: {exc}")], "counts": counts}
    con.close()
    checks.append(_item("Database file", res == ["ok"], "SQLite integrity check passed" if res == ["ok"] else "; ".join(res[:5])))

    engine = create_engine(f"sqlite:///file:{db_path}?immutable=1&uri=true")
    try:
        # 2. upgrade level
        v = migrations.current_version(engine)
        checks.append(_item("Database version", v <= migrations.SCHEMA_VERSION,
                            f"version {v} (this Benchtop understands up to {migrations.SCHEMA_VERSION})"))
        with Session(engine) as s:
            # 3. audit trail
            chain = audit.verify_chain(s)
            counts["audit_events"] = chain["checked"]
            fork_note = (f" ({chain['forks']} pair{'s' if chain['forks'] != 1 else ''} written at the same moment by an older version;"
                         " both intact, not tampering)") if chain.get("forks") else ""
            checks.append(_item("Audit trail", chain["ok"],
                                f"{chain['checked']:,} events, unbroken{fork_note}; latest fingerprint {chain['head'][:16]}…" if chain["ok"]
                                else f"broken at event {chain.get('broken_at')} ({chain.get('reason')})"))
            # 4. signed records still match their signatures
            bad, n = [], 0
            for exp in s.exec(select(Experiment).where(Experiment.status.in_([ExperimentStatus.signed, ExperimentStatus.witnessed]))):
                n += 1
                sig = s.exec(select(Signature).where(Signature.experiment_id == exp.id, Signature.meaning.in_(["author", "witness"]))
                             .order_by(Signature.id.desc())).first()
                if sig is None or sig.content_hash != content_hash(s, exp):
                    bad.append(exp.eid)
            counts["signed_records"] = n
            checks.append(_item("Signed records", not bad, f"all {n:,} match their signatures" if not bad
                                else f"{len(bad)} of {n} no longer match: {', '.join(bad[:10])}{'…' if len(bad) > 10 else ''}"))
            # 5. attached files
            missing, damaged, n = [], [], 0
            for a in s.exec(select(Attachment)):
                n += 1
                p = attachments_dir / a.sha256[:2] / a.sha256
                if not p.is_file():
                    missing.append(a.filename)
                elif hashlib.sha256(p.read_bytes()).hexdigest() != a.sha256:
                    damaged.append(a.filename)
            counts["attachments"] = n
            detail = f"all {n:,} present and unchanged"
            if missing or damaged:
                detail = "; ".join(x for x in (f"{len(missing)} missing ({', '.join(missing[:5])})" if missing else "",
                                               f"{len(damaged)} changed ({', '.join(damaged[:5])})" if damaged else "") if x)
            checks.append(_item("Attached files", not (missing or damaged), detail))
    finally:
        engine.dispose()
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "counts": counts,
            "audit_head": chain["head"] if chain["ok"] else None}


def check_live() -> dict[str, Any]:
    """Check the running data via a consistent snapshot, so the server can keep running."""
    from .backup import _sqlite_path
    from .config import settings

    with tempfile.TemporaryDirectory(prefix="benchtop-check-") as tmp:
        snap = Path(tmp) / "snapshot.db"
        src, dst = sqlite3.connect(_sqlite_path()), sqlite3.connect(snap)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return check_files(snap, settings.attachments_dir)


def check_backup(zip_path: Path) -> dict[str, Any]:
    """Unpack a backup to a temporary folder and check it as if it had been restored."""
    with tempfile.TemporaryDirectory(prefix="benchtop-backup-test-") as tmp:
        root = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path) as z:
                names = z.namelist()
                if "benchtop.db" not in names or "manifest.json" not in names:
                    return {"ok": False, "checks": [_item("Backup file", False, "Not a Benchtop backup")], "counts": {}}
                manifest = json.loads(z.read("manifest.json"))
                bad = z.testzip()
                if bad:
                    return {"ok": False, "checks": [_item("Backup file", False, f"Damaged inside the zip: {bad}")], "counts": {}}
                (root / "benchtop.db").write_bytes(z.read("benchtop.db"))
                att = root / "attachments"
                for n in names:
                    if n.startswith("attachments/") and not n.endswith("/"):
                        t = (att / n.removeprefix("attachments/")).resolve()
                        if att.resolve() not in t.parents:
                            return {"ok": False, "checks": [_item("Backup file", False, f"Unsafe path inside: {n}")], "counts": {}}
                        t.parent.mkdir(parents=True, exist_ok=True)
                        t.write_bytes(z.read(n))
        except (zipfile.BadZipFile, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "checks": [_item("Backup file", False, f"Couldn't read it: {exc}")], "counts": {}}
        db_ok = hashlib.sha256((root / "benchtop.db").read_bytes()).hexdigest() == manifest.get("database_sha256")
        first = _item("Backup file", db_ok, f"unpacked; made by Benchtop {manifest.get('app_version', '?')}" if db_ok
                      else "the database inside doesn't match its checksum")
        if not db_ok:
            return {"ok": False, "checks": [first], "counts": {}}
        res = check_files(root / "benchtop.db", root / "attachments")
        res["checks"].insert(0, first)
        res["manifest"] = manifest
        return res


def report(res: dict[str, Any]) -> str:
    lines = [f"  {'✓' if c['ok'] else '✗'} {c['name']}: {c['detail']}" for c in res["checks"]]
    lines.append("All good." if res["ok"] else "Problems found — see above. Your latest backups are in the backups folder.")
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    if len(argv) > 1:
        p = Path(argv[1]).expanduser()
        print(f"Checking backup {p.name}…")
        res = check_backup(p)
    else:
        print("Checking your Benchtop data…")
        res = check_live()
    print(report(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
