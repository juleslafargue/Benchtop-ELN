"""v0.9.1: password rules, guessing limits, sessions, forced password changes, setup and headers."""
import time

from sqlmodel import Session, select

from app.db import engine
from app.models import AuthFailure, User
from app.security import hash_password, verify_password
from tests.conftest import _login

PW = "lab-bench-pw-2026"


def _new(client, admin, name, **kw):
    r = client.post("/api/users", headers=admin, json={"username": name, "password": PW, "full_name": name.title(), "must_change": False, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _clear_failures():
    with Session(engine) as s:
        for f in s.exec(select(AuthFailure)):
            s.delete(f)
        s.commit()


def test_password_rules_everywhere(client, admin):
    pc = client.post("/api/auth/password-check", json={"password": "password1!", "username": "x"}).json()
    assert pc["problems"] and "common" in pc["problems"][0]
    for bad, why in (("short", "10 characters"), ("iloveyou12", "common"), ("emmanuel-the-chemist", "username"),
                     ("aaaaaaaaaaaa", "repeat"), ("qwertyuiopas", "")):
        r = client.post("/api/users", headers=admin, json={"username": "emmanuel", "password": bad, "full_name": "Emmanuel Ito"})
        assert r.status_code == 422, bad
        assert why.lower() in r.json()["detail"].lower(), (bad, r.json()["detail"])
    assert client.post("/api/auth/password-check", json={"password": "correct horse battery staple"}).json()["problems"] == []
    # admin resets are held to the same rules
    u = _new(client, admin, "rulesy")
    assert client.patch(f"/api/users/{u['id']}", headers=admin, json={"password": "12345678"}).status_code == 422


def test_guessing_is_throttled_and_usernames_dont_leak(client, admin):
    _new(client, admin, "target")
    _clear_failures()
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "target", "password": "wrong-guess-123"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "target", "password": PW})
    assert r.status_code == 429 and "try again in" in r.json()["detail"]          # even the right password waits
    # the same message and similar time for an account that doesn't exist
    t0 = time.perf_counter(); a = client.post("/api/auth/login", json={"username": "nobody-here", "password": "x" * 12}); t_missing = time.perf_counter() - t0
    t0 = time.perf_counter(); b = client.post("/api/auth/login", json={"username": "admin", "password": "x" * 12}); t_real = time.perf_counter() - t0
    assert a.json()["detail"] == b.json()["detail"] == "Invalid username or password"
    assert t_missing > 0.4 * t_real
    _clear_failures()
    assert client.post("/api/auth/login", json={"username": "target", "password": PW}).status_code == 200


def test_signature_guessing_is_throttled(client, admin, users, notebook):
    from tests.test_v2 import new_exp
    _clear_failures()
    exp = new_exp(client, users["alice"], notebook)
    for _ in range(5):
        assert client.post(f"/api/experiments/{exp['id']}/sign", headers=users["alice"], json={"password": "not-my-password"}).status_code == 401
    r = client.post(f"/api/experiments/{exp['id']}/sign", headers=users["alice"], json={"password": PW})
    assert r.status_code == 429
    _clear_failures()
    assert client.post(f"/api/experiments/{exp['id']}/sign", headers=users["alice"], json={"password": PW}).status_code == 200


def test_sessions_can_be_listed_and_ended(client, admin):
    _new(client, admin, "roamer")
    laptop, phone = _login(client, "roamer", PW), _login(client, "roamer", PW)
    sess = client.get("/api/auth/sessions", headers=laptop).json()
    assert len(sess) == 2 and sum(s["current"] for s in sess) == 1
    # end the phone's session from the laptop
    other = next(s for s in sess if not s["current"])
    assert client.delete(f"/api/auth/sessions/{other['id']}", headers=laptop).status_code == 200
    assert client.get("/api/auth/me", headers=phone).status_code == 401
    # logout really ends the session server-side
    assert client.post("/api/auth/logout", headers=laptop).status_code == 200
    assert client.get("/api/auth/me", headers=laptop).status_code == 401
    # changing the password signs out everywhere else, but not here
    a, b = _login(client, "roamer", PW), _login(client, "roamer", PW)
    r = client.post("/api/auth/password", headers=a, json={"current_password": PW, "new_password": "brand-new-river-stone"})
    assert r.status_code == 200 and r.json()["other_sessions_ended"] == 1
    assert client.get("/api/auth/me", headers=a).status_code == 200 and client.get("/api/auth/me", headers=b).status_code == 401
    assert client.post("/api/auth/password", headers=a, json={"current_password": "brand-new-river-stone", "new_password": "brand-new-river-stone"}).status_code == 422
    # sign out everywhere else
    c = _login(client, "roamer", "brand-new-river-stone")
    assert client.post("/api/auth/sessions/end-others", headers=c).json()["ended"] == 1
    assert client.get("/api/auth/me", headers=a).status_code == 401


def test_old_style_tokens_stop_working(client):
    from app.security import create_token
    with Session(engine) as s:
        uid = s.exec(select(User).where(User.username == "admin")).one().id
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {create_token(uid)}"}).status_code == 401


def test_admin_reset_and_deactivate(client, admin):
    u = _new(client, admin, "forgetful")
    h = _login(client, "forgetful", PW)
    client.patch(f"/api/users/{u['id']}", headers=admin, json={"password": "temporary-green-door"})
    assert client.get("/api/auth/me", headers=h).status_code == 401                 # reset ends their sessions
    h = _login(client, "forgetful", "temporary-green-door")
    me = client.get("/api/auth/me", headers=h).json()
    assert me["must_change_password"] is True
    assert client.get("/api/notebooks", headers=h).status_code == 403              # nothing else until they choose one
    assert client.post("/api/auth/password", headers=h, json={"current_password": "", "new_password": "my-own-secret-path"}).status_code == 200
    assert client.get("/api/notebooks", headers=h).status_code == 200
    # admin-created accounts choose their own password by default
    client.post("/api/users", headers=admin, json={"username": "newbie", "password": "handed-over-pass-1", "full_name": "New Bie"})
    nh = _login(client, "newbie", "handed-over-pass-1")
    assert client.get("/api/auth/me", headers=nh).json()["must_change_password"] is True
    # deactivating, or "sign out everywhere", ends sessions
    h2 = _login(client, "forgetful", "my-own-secret-path")
    assert client.post(f"/api/users/{u['id']}/sign-out", headers=admin).json()["ended"] >= 1
    assert client.get("/api/auth/me", headers=h2).status_code == 401
    h3 = _login(client, "forgetful", "my-own-secret-path")
    client.patch(f"/api/users/{u['id']}", headers=admin, json={"active": False})
    assert client.get("/api/auth/me", headers=h3).status_code == 401


def test_older_password_hashes_are_upgraded_on_sign_in(client, admin):
    import hashlib
    u = _new(client, admin, "oldtimer")
    salt = b"0123456789abcdef"
    old = f"pbkdf2_sha256$390000${salt.hex()}${hashlib.pbkdf2_hmac('sha256', PW.encode(), salt, 390000).hex()}"
    with Session(engine) as s:
        row = s.get(User, u["id"]); row.password_hash = old; s.add(row); s.commit()
    _login(client, "oldtimer", PW)
    with Session(engine) as s:
        h = s.get(User, u["id"]).password_hash
    assert h.split("$")[1] == "600000" and verify_password(PW, h)


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"


def test_default_admin_cannot_be_used_before_setup(tmp_path, monkeypatch):
    """A fresh Mac install (admin/admin) on a shared network: nobody can sign in as admin, and only
    this computer can do the first-run setup."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.routers import auth
    monkeypatch.setattr(settings, "admin_password", "admin")
    monkeypatch.setattr(auth, "_needs_setup", lambda session: True)
    c = TestClient(app)
    with Session(engine) as s:
        a = s.exec(select(User).where(User.username == "admin")).one()
        saved = a.password_hash
        a.password_hash = hash_password("admin"); s.add(a); s.commit()
    try:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 403 and "setting up" in r.json()["detail"]
        monkeypatch.setattr(auth, "_is_local", lambda request: False)
        r = c.post("/api/setup", json={"full_name": "Mallory", "username": "mallory", "password": "violet-anvil-river"})
        assert r.status_code == 403 and "computer Benchtop is running on" in r.json()["detail"]
    finally:
        with Session(engine) as s:
            a = s.exec(select(User).where(User.username == "admin")).one(); a.password_hash = saved; s.add(a); s.commit()


def test_sidebar_order_is_saved_and_cleaned(client, admin):
    r = client.patch("/api/auth/me", headers=admin, json={"preferences": {"nav": ["compounds", "dashboard", "compounds", "<script>"], "nav_hidden": ["groups", 7]}})
    p = r.json()["preferences"]
    assert p["nav"] == ["compounds", "dashboard"] and p["nav_hidden"] == ["groups"]
    client.patch("/api/auth/me", headers=admin, json={"preferences": {"nav": [], "nav_hidden": []}})


def test_rich_text_keeps_formatting_but_not_scripts():
    from app.routers.experiments import clean_html
    out = clean_html('<ul data-type="taskList"><li data-type="taskItem" data-checked="true"><div><p style="text-align:center;position:fixed">'
                     '<span style="color:#c0392b">Dry</span> <mark>MgSO4</mark></p></div></li></ul>'
                     '<a href="javascript:alert(1)" onclick="x">bad</a><img src=x onerror=alert(1)><script>alert(1)</script>')
    assert 'data-checked="true"' in out and 'text-align:center' in out and 'color:#c0392b' in out and "<mark>" in out
    assert "position" not in out and "javascript" not in out and "onerror" not in out and "onclick" not in out and "<script" not in out
