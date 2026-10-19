from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import audit, backup
from ..config import settings
from ..security import DB, Admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/backups")
def list_backups(_: Admin):
    return {"directory": str(settings.backup_dir), "keep": settings.backup_keep,
            "interval_hours": settings.backup_interval_hours, "backups": backup.list_backups(),
            **backup.health(), "suggestions": backup.suggested_copy_dirs()}


class CopyDirIn(BaseModel):
    path: str | None = None


@router.put("/backups/copy-dir")
async def set_backup_copy_dir(body: CopyDirIn, user: Admin, session: DB):
    """Where a second copy of every backup goes (iCloud Drive, an external drive…); empty to stop."""
    if "BENCHTOP_BACKUP_COPY_DIR" in os.environ:
        raise HTTPException(409, "The second-copy folder is set by BENCHTOP_BACKUP_COPY_DIR in this server's settings")
    try:
        await run_in_threadpool(backup.set_copy_dir, (body.path or "").strip() or None)
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc))
    audit.record(session, user, "backup_copy_dir", "system", None, path=body.path or "")
    session.commit()
    first = None
    newest = backup.list_backups()
    if body.path and newest:  # copy the newest one straight away, so there's a copy from today
        first = await run_in_threadpool(backup.copy_to_second_place, Path(newest[0]["path"]))
    return {**backup.health(), "first_copy": first}


@router.post("/backups/test")
async def test_backup(user: Admin, session: DB):
    """Unpack the newest backup and check it as if it had been restored."""
    res = await run_in_threadpool(backup.test_latest_backup)
    if res is None:
        raise HTTPException(409, "There are no backups to test yet; make one first")
    audit.record(session, user, "backup_test", "system", None, name=res["name"], ok=res["ok"])
    session.commit()
    return res


@router.post("/backups", status_code=201)
async def create_backup(user: Admin, session: DB):
    try:
        b = await run_in_threadpool(backup.create_backup, f"manual by {user.username}")
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc))
    audit.record(session, user, "backup", "system", None, name=b["name"], size=b["size"])
    session.commit()
    return b
