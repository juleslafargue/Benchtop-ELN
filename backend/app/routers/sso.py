"""Single sign-on with OpenID Connect (authorization code flow with PKCE).

Works with any standard provider: Microsoft Entra ID (Microsoft 365 / university accounts),
Google Workspace, Okta, Auth0, Keycloak, Authentik… Configure with BENCHTOP_OIDC_ISSUER,
BENCHTOP_OIDC_CLIENT_ID and BENCHTOP_OIDC_CLIENT_SECRET; register the redirect URI
<public URL>/api/auth/sso/callback with the provider.

The flow is stateless on our side: state, nonce and the PKCE verifier travel in a short-lived,
HMAC-signed cookie. Tokens come straight from the provider's token endpoint over TLS (so the
ID token's claims can be trusted without verifying its signature, OIDC Core §3.1.3.7), and the
user's details are read from the userinfo endpoint when there is one.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import select

from .. import __version__, audit
from ..config import settings
from ..models import Role, User, UserIdentity, utcnow
from ..security import DB, SSO_ONLY, start_session

router = APIRouter(prefix="/api/auth/sso", tags=["single sign-on"])
COOKIE = "benchtop_sso"
_discovery: dict[str, Any] = {}


def enabled() -> bool:
    return bool(settings.oidc_issuer and settings.oidc_client_id)


def provider_name() -> str:
    if settings.oidc_name:
        return settings.oidc_name
    iss = settings.oidc_issuer.lower()
    for key, name in (("microsoftonline", "Microsoft"), ("login.microsoft", "Microsoft"), ("accounts.google", "Google"),
                      ("okta", "Okta"), ("auth0", "Auth0")):
        if key in iss:
            return name
    return "single sign-on"


def _client() -> httpx.Client:
    return httpx.Client(timeout=15.0, headers={"User-Agent": f"Benchtop-ELN/{__version__}"}, follow_redirects=True)


def discovery() -> dict[str, Any]:
    cached = _discovery.get(settings.oidc_issuer)
    if cached and cached["_at"] > time.time() - 3600:
        return cached
    try:
        with _client() as c:
            r = c.get(f"{settings.oidc_issuer}/.well-known/openid-configuration")
            r.raise_for_status()
            d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"Couldn't reach the sign-in provider ({e.__class__.__name__}); try again, or sign in with a password") from e
    for k in ("authorization_endpoint", "token_endpoint"):
        if not d.get(k):
            raise HTTPException(502, f"The sign-in provider's configuration has no {k}")
    d["_at"] = time.time()
    _discovery[settings.oidc_issuer] = d
    return d


def _sign(payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(settings.secret_key.encode(), f"sso.{body}".encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def _unsign(value: str) -> dict[str, Any] | None:
    try:
        body, mac = value.rsplit(".", 1)
        if not hmac.compare_digest(mac, hmac.new(settings.secret_key.encode(), f"sso.{body}".encode(), hashlib.sha256).hexdigest()):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data.get("exp", 0) > time.time() else None
    except (ValueError, json.JSONDecodeError):
        return None


def _base(request: Request) -> str:
    return settings.public_url or str(request.base_url).rstrip("/")


def _claims_from_id_token(tok: str) -> dict[str, Any]:
    try:
        part = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def _fail(request: Request, msg: str) -> RedirectResponse:
    r = RedirectResponse(f"{_base(request)}/#{urlencode({'sso_error': msg})}", status_code=302)
    r.delete_cookie(COOKIE, path="/api/auth/sso")
    return r


@router.get("")
def sso_info():
    return {"enabled": enabled(), "name": provider_name() if enabled() else None, "password_login": settings.password_login}


@router.get("/start")
def start(request: Request, remember: bool = True):
    if not enabled():
        raise HTTPException(404, "Single sign-on isn't set up on this Benchtop")
    d = discovery()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    params = {"response_type": "code", "client_id": settings.oidc_client_id, "redirect_uri": f"{_base(request)}/api/auth/sso/callback",
              "scope": "openid email profile", "state": state, "nonce": nonce,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    r = RedirectResponse(f"{d['authorization_endpoint']}?{urlencode(params)}", status_code=302)
    r.set_cookie(COOKIE, _sign({"s": state, "n": nonce, "v": verifier, "r": remember, "exp": time.time() + 600}),
                 max_age=600, httponly=True, samesite="lax", secure=_base(request).startswith("https://"), path="/api/auth/sso")
    return r


def _username_for(session, email: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9._-]", "", (email.split("@")[0] if email else name.replace(" ", ".")).lower())[:28] or "user"
    if not base[0].isalnum():
        base = "u" + base
    cand, n = base, 1
    while session.exec(select(User).where(User.username == cand)).first():
        n += 1
        cand = f"{base}{n}"
    return cand


@router.get("/callback")
def callback(request: Request, session: DB, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return _fail(request, error_description or error)
    saved = _unsign(request.cookies.get(COOKIE, ""))
    if not saved or not state or not hmac.compare_digest(saved["s"], state):
        return _fail(request, "The sign-in took too long or came from another window. Please try again.")
    d = discovery()
    try:
        with _client() as c:
            tr = c.post(d["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": f"{_base(request)}/api/auth/sso/callback",
                "client_id": settings.oidc_client_id, "client_secret": settings.oidc_client_secret, "code_verifier": saved["v"]})
            if tr.status_code != 200:
                return _fail(request, f"The sign-in provider refused the login ({tr.json().get('error_description') or tr.json().get('error') if tr.headers.get('content-type', '').startswith('application/json') else tr.status_code})")
            tok = tr.json()
            claims = _claims_from_id_token(tok.get("id_token", ""))
            if claims.get("nonce") and not hmac.compare_digest(str(claims["nonce"]), saved["n"]):
                return _fail(request, "The sign-in response didn't match this request. Please try again.")
            if d.get("userinfo_endpoint") and tok.get("access_token"):
                ur = c.get(d["userinfo_endpoint"], headers={"Authorization": f"Bearer {tok['access_token']}"})
                if ur.status_code == 200:
                    claims = {**claims, **ur.json()}
    except httpx.HTTPError as e:
        return _fail(request, f"Couldn't reach the sign-in provider ({e.__class__.__name__})")

    sub = str(claims.get("sub") or claims.get("oid") or "")
    email = str(claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    name = str(claims.get("name") or " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x) or "").strip()
    if not sub:
        return _fail(request, "The sign-in provider didn't say who you are")
    if claims.get("email_verified") is False:
        return _fail(request, "Your email address isn't verified with the sign-in provider")
    if settings.oidc_domains and email.split("@")[-1] not in settings.oidc_domains:
        audit.record(session, None, "login_failed", "user", None, username=email, method="sso", reason="domain not allowed")
        session.commit()
        return _fail(request, f"Accounts from {email.split('@')[-1] or 'that provider'} can't sign in here")

    issuer = str(claims.get("iss") or settings.oidc_issuer)
    ident = session.exec(select(UserIdentity).where(UserIdentity.issuer == issuer, UserIdentity.subject == sub)).first()
    user = session.get(User, ident.user_id) if ident else None
    created = False
    if user is None and email:  # first sign-in: link to an existing account with the same email
        user = next((u for u in session.exec(select(User)) if (u.email or "").lower() == email), None)
    if user is None:
        if not settings.oidc_auto_create:
            audit.record(session, None, "login_failed", "user", None, username=email, method="sso", reason="no account")
            session.commit()
            return _fail(request, f"There's no Benchtop account for {email or 'you'} yet. Ask an admin to add one with this email address.")
        role = Role.viewer if settings.oidc_default_role == "viewer" else Role.scientist
        user = User(username=_username_for(session, email, name), full_name=name or email, email=email if "@" in email else "",
                    role=role, password_hash=SSO_ONLY + secrets.token_hex(8))
        session.add(user)
        session.flush()
        created = True
        audit.record(session, None, "create", "user", user.id, username=user.username, role=role.value, method="sso", issuer=issuer)
    if not user.active:
        return _fail(request, "This account has been deactivated")
    if ident is None:
        ident = UserIdentity(user_id=user.id, issuer=issuer, subject=sub, email=email)
    ident.last_login_at = utcnow()
    session.add(ident)
    token = start_session(session, user, request, 24 * 30 if saved.get("r") else None, method="sso")
    audit.record(session, user, "login", "user", user.id, method="sso", new_account=created)
    session.commit()
    r = RedirectResponse(f"{_base(request)}/#{urlencode({'sso': token, **({'welcome': '1'} if created else {})})}", status_code=302)
    r.delete_cookie(COOKIE, path="/api/auth/sso")
    return r
