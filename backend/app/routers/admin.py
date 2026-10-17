from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from .. import audit, backup
from ..config import settings
from ..security import DB, Admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/backups")
def list_backups(_: Admin):
    return {"directory": str(settings.backup_dir), "keep": settings.backup_keep,
            "interval_hours": settings.backup_interval_hours, "backups": backup.list_backups()}


@router.post("/backups", status_code=201)
async def create_backup(user: Admin, session: DB):
    try:
        b = await run_in_threadpool(backup.create_backup, f"manual by {user.username}")
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc))
    audit.record(session, user, "backup", "system", None, name=b["name"], size=b["size"])
    session.commit()
    return b
