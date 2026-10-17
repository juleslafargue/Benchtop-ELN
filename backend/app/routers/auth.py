from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select

from .. import audit
from ..models import Role, User
from ..config import settings
from ..security import DB, Admin, CurrentUser, create_token, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = False  # keep me signed in for 30 days


REMEMBER_HOURS = 24 * 30


def user_out(u: User) -> dict:
    from ..security import has_password
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email,
            "role": u.role.value, "active": u.active, "preferences": u.preferences or {},
            "has_password": has_password(u.password_hash)}


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
PREF_KEYS = {"theme": ("system", "light", "dark"), "default_notebook_id": None, "spectrum_expanded": (True, False)}


def _check_email(email: str) -> str:
    email = email.strip()
    if email and not EMAIL.match(email):
        raise HTTPException(422, "That doesn't look like an email address")
    return email


def _check_username(session, name: str, me: int | None = None) -> str:
    name = name.strip().lower()
    if not USERNAME.match(name):
        raise HTTPException(422, "Usernames are 2–32 characters: lower-case letters, digits, '.', '_' or '-'")
    other = session.exec(select(User).where(User.username == name)).first()
    if other and other.id != me:
        raise HTTPException(409, "That username is taken")
    return name


WIDGET_TYPES = {"list", "actions", "note", "header", "yield", "month", "spectra", "inventory"}
LIST_KINDS = {"recent", "open", "awaiting", "returned", "pinned"}


def _clean_dashboard(d: Any) -> dict[str, Any] | None:
    if not isinstance(d, dict) or not isinstance(d.get("widgets"), list):
        return None
    widgets = []
    for w in d["widgets"][:40]:
        if not isinstance(w, dict) or w.get("type") not in WIDGET_TYPES:
            continue
        num = lambda k, lo, hi, dflt: max(lo, min(hi, int(w.get(k, dflt)) if isinstance(w.get(k, dflt), (int, float)) else dflt))  # noqa: E731
        item = {"i": str(w.get("i") or "")[:40] or f"w{len(widgets)}", "type": w["type"],
                "x": num("x", 0, 11, 0), "y": num("y", 0, 500, 0), "w": num("w", 2, 12, 4), "h": num("h", 2, 20, 4)}
        if w["type"] == "list":
            item["list"] = w.get("list") if w.get("list") in LIST_KINDS else "recent"
        if w["type"] in ("note", "header"):
            item["text"] = str(w.get("text") or "")[:4000]
        if isinstance(w.get("title"), str):
            item["title"] = w["title"][:80]
        widgets.append(item)
    return {"widgets": widgets}


def _clean_prefs(p: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in p.items():
        if k == "dashboard":
            cleaned = _clean_dashboard(v)
            out[k] = cleaned
            continue
        if k == "pinned":
            out[k] = [int(i) for i in (v or [])[:100] if isinstance(i, (int, float))] if isinstance(v, list) else []
            continue
        if k not in PREF_KEYS:
            continue
        allowed = PREF_KEYS[k]
        if allowed is None:
            out[k] = int(v) if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()) else None
        elif v in allowed:
            out[k] = v
    return out


class ProfileIn(BaseModel):
    full_name: str | None = None
    email: str | None = None
    preferences: dict[str, Any] | None = None


@router.patch("/auth/me")
def update_me(body: ProfileIn, user: CurrentUser, session: DB):
    changes: dict[str, Any] = {}
    if body.full_name is not None:
        name = body.full_name.strip()
        if not name:
            raise HTTPException(422, "Your name can't be empty; it appears on your signatures")
        if name != user.full_name:
            changes["full_name"] = {"from": user.full_name, "to": name}
            user.full_name = name
    if body.email is not None:
        email = _check_email(body.email)
        if email != user.email:
            changes["email"] = {"from": user.email, "to": email}
            user.email = email
    if body.preferences is not None:
        prefs = {**(user.preferences or {}), **_clean_prefs(body.preferences)}
        if prefs != (user.preferences or {}):
            user.preferences = prefs
            changes["preferences"] = prefs
    if changes:
        session.add(user)
        audit.record(session, user, "update_profile", "user", user.id, changes=changes)
        session.commit()
    return user_out(user)


class UsernameIn(BaseModel):
    new_username: str
    password: str


@router.post("/auth/username")
def change_username(body: UsernameIn, user: CurrentUser, session: DB):
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password is wrong")
    new = _check_username(session, body.new_username, user.id)
    old = user.username
    if new != old:
        user.username = new
        session.add(user)
        audit.record(session, user, "change_username", "user", user.id, old=old, new=new)
        session.commit()
    return user_out(user)


@router.post("/auth/login")
def login(body: LoginIn, session: DB):
    user = (session.exec(select(User).where(User.username == body.username)).first()
            or session.exec(select(User).where(User.username == body.username.strip().lower())).first())
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        audit.record(session, None, "login_failed", "user", user.id if user else None,
                     username=body.username)
        session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if not settings.password_login and user.role != Role.admin:  # admins keep a password as a way in if SSO breaks
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This Benchtop uses single sign-on; use the sign-in button instead")
    audit.record(session, user, "login", "user", user.id, remember=body.remember)
    session.commit()
    return {"token": create_token(user.id, REMEMBER_HOURS if body.remember else None), "user": user_out(user)}


# ------------------------------------------------------------------ first-run setup

def _needs_setup(session) -> bool:
    """True on a fresh install: the only account is the built-in admin, still on its default password."""
    users = session.exec(select(User)).all()
    if len(users) != 1:
        return False
    u = users[0]
    return u.username == settings.admin_username and u.active and verify_password(settings.admin_password, u.password_hash)


@router.get("/setup")
def setup_status(session: DB):
    return {"needs_setup": _needs_setup(session)}


class SetupIn(BaseModel):
    full_name: str
    username: str
    email: str = ""
    password: str
    load_demo: bool = False


@router.post("/setup", status_code=201)
def run_setup(body: SetupIn, session: DB):
    """Create the first real account (an admin) and retire the default admin login."""
    if not _needs_setup(session):
        raise HTTPException(409, "Setup has already been done; sign in instead")
    if not body.full_name.strip():
        raise HTTPException(422, "Your name appears on your signatures, so it can't be empty")
    if len(body.password) < 8:
        raise HTTPException(422, "Use at least 8 characters for your password")
    username = _check_username(session, body.username)
    default = session.exec(select(User)).one()
    if username == default.username:
        raise HTTPException(422, f"Pick a username other than '{default.username}'")
    me = User(username=username, full_name=body.full_name.strip(), email=_check_email(body.email),
              role=Role.admin, password_hash=hash_password(body.password))
    session.add(me)
    session.flush()
    default.active = False  # kept (not deleted) so earlier audit entries still resolve
    session.add(default)
    audit.record(session, me, "setup", "user", me.id, username=me.username, retired_default=default.username)
    session.commit()
    demo = False
    if body.load_demo:
        from fastapi.testclient import TestClient

        from ..main import app
        from ..seed import load_demo
        demo = load_demo(TestClient(app))
    return {"token": create_token(me.id, REMEMBER_HOURS), "user": user_out(me), "demo_loaded": demo}


@router.get("/auth/me")
def me(user: CurrentUser):
    return user_out(user)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/password")
def change_password(body: PasswordIn, user: CurrentUser, session: DB):
    """Change your password, or (for an account made by single sign-on) set one: signatures need it."""
    from ..security import has_password
    if has_password(user.password_hash) and not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong")
    if len(body.new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use at least 8 characters")
    user.password_hash = hash_password(body.new_password)
    session.add(user)
    audit.record(session, user, "password_changed", "user", user.id)
    session.commit()
    return {"ok": True}


# ------------------------------------------------------------------ user admin

@router.get("/users")
def list_users(_: CurrentUser, session: DB):
    return [user_out(u) for u in session.exec(select(User).order_by(User.username))]


class UserIn(BaseModel):
    username: str
    password: str
    full_name: str = ""
    email: str = ""
    role: Role = Role.scientist


@router.post("/users", status_code=201)
def create_user(body: UserIn, actor: Admin, session: DB):
    if session.exec(select(User).where(User.username == body.username)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username taken")
    u = User(username=_check_username(session, body.username), full_name=body.full_name, email=_check_email(body.email),
             role=body.role, password_hash=hash_password(body.password))
    session.add(u)
    session.flush()
    audit.record(session, actor, "create", "user", u.id, username=u.username, role=u.role.value)
    session.commit()
    return user_out(u)


class UserPatch(BaseModel):
    username: str | None = None
    full_name: str | None = None
    email: str | None = None
    role: Role | None = None
    active: bool | None = None
    password: str | None = None


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserPatch, actor: Admin, session: DB):
    u = session.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    changes = body.model_dump(exclude_unset=True)
    if "username" in changes:
        changes["username"] = _check_username(session, changes["username"], u.id)
    if "email" in changes and changes["email"] is not None:
        changes["email"] = _check_email(changes["email"])
    if "password" in changes:
        u.password_hash = hash_password(changes.pop("password"))
        changes["password"] = "(reset)"
    for k, v in changes.items():
        if k != "password":
            setattr(u, k, v)
    session.add(u)
    audit.record(session, actor, "update", "user", u.id, changes=changes)
    session.commit()
    return user_out(u)
