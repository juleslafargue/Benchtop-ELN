from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from . import __version__, backup
from .config import settings
from .db import engine, init_db
from .models import Role, User
from .routers import admin, auth, chemistry, dashboard, experiments, materials, reactions, safety as safety_router, search, nmr as nmr_router, groups, comments, sso, integrations, bench
from .security import hash_password


def ensure_admin() -> None:
    with Session(engine) as s:
        if s.exec(select(User)).first() is None:
            s.add(User(username=settings.admin_username, full_name="Administrator", role=Role.admin,
                       password_hash=hash_password(settings.admin_password)))
            s.commit()


log = logging.getLogger("benchtop")


def _refresh_safety() -> dict | None:
    from sqlmodel import Session

    from . import safety
    from .db import engine
    with Session(engine, expire_on_commit=False) as s:
        return safety.refresh_due(s)


def _sync_obsidian() -> dict | None:
    if not settings.obsidian_dir:
        return None
    from .export_md import sync_vault

    with Session(engine) as s:
        return sync_vault(s, settings.obsidian_dir, base_url=f"http://localhost:{settings.port}")


async def _housekeeping_loop() -> None:
    while True:
        try:
            made = await asyncio.to_thread(backup.maybe_backup)
            if made:
                log.info("Backup written: %s", made["path"])
        except Exception:  # never let a backup problem take the server down
            log.exception("Automatic backup failed")
        try:
            synced = await asyncio.to_thread(_sync_obsidian)
            if synced and synced["written"]:
                log.info("Obsidian sync: %s notes updated", synced["written"])
        except Exception:
            log.exception("Obsidian sync failed")
        try:
            from . import nmrdata
            r = await asyncio.to_thread(nmrdata.maybe_update)
            if r and r.get("updated"):
                log.info("NMR shift table rebuilt from NMRShiftDB2: %s structures", r["molecules"])
        except Exception:
            log.exception("NMR data update failed")
        try:
            res = await asyncio.to_thread(_refresh_safety)
            if res and res["checked"]:
                log.info("Safety data: %s checked, %s changed", res["checked"], res["changed"])
        except Exception:
            log.exception("Safety data refresh failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    ensure_admin()
    task = asyncio.create_task(_housekeeping_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Benchtop ELN", version=__version__, lifespan=lifespan,
              description="Open-source electronic lab notebook for chemistry.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# Browser security headers. The policy allows only this server's own scripts (the structure editor
# also needs WebAssembly and generated validation code, hence the eval allowances; inline and
# third-party scripts stay blocked), Google Fonts, and images from this server, data: and blob: URLs, so an
# injected <script> or a malicious file can't run or phone home; pages can't be framed by other sites.
CSP = "; ".join([
    "default-src 'self'", "script-src 'self' 'wasm-unsafe-eval' 'unsafe-eval'", "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:", "img-src 'self' data: blob: https://pubchem.ncbi.nlm.nih.gov", "connect-src 'self'",
    "worker-src 'self' blob:", "frame-src 'self' blob:", "object-src 'none'", "base-uri 'self'", "form-action 'self'",
    "frame-ancestors 'none'",
])


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if not request.url.path.startswith("/docs") and not request.url.path.startswith("/redoc"):
        h.setdefault("Content-Security-Policy", CSP)
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")  # nothing from the API lingers in shared browser caches
    return resp


for r in (auth, experiments, materials, chemistry, search, reactions, admin, dashboard, safety_router, nmr_router, groups, comments, sso, integrations, bench):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "demo": settings.demo}


@app.get("/api/demo")
def demo_accounts():
    """Demo mode only: who to sign in as."""
    if not settings.demo:
        return {"enabled": False}
    from .demo_lab import PEOPLE, PW
    return {"enabled": True, "password": PW, "accounts": [{"username": u, "full_name": n, "role": r, "about": a} for u, n, r, a in PEOPLE]}


# Serve the built single-page app when BENCHTOP_STATIC_DIR is set (Docker image does this)
if settings.static_dir and Path(settings.static_dir).is_dir():
    static = Path(settings.static_dir)
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = static / path
        if path and f.is_file() and static in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(static / "index.html")
