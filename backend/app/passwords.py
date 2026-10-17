"""Password rules, following NIST SP 800-63B: length matters, composition rules don't.

A new password must be at least 10 characters, not one of the 10,000 most common passwords
(or one of those with a digit or symbol tacked on), not built from the person's own username,
name or email (or "benchtop"), and not a run of one character or a keyboard/number sequence. No forced mixes of
upper case, digits and symbols, and no expiry: those make passwords weaker in practice.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

MIN_LEN = 10
MAX_LEN = 256
_COMMON = Path(__file__).parent / "data" / "common_passwords.txt"
_SEQUENCES = ("abcdefghijklmnopqrstuvwxyz", "qwertyuiopasdfghjklzxcvbnm", "01234567890", "1qaz2wsx3edc4rfv5tgb")


@lru_cache(maxsize=1)
def common() -> frozenset[str]:
    try:
        return frozenset(w.strip() for w in _COMMON.read_text(encoding="utf-8").splitlines() if w and not w.startswith("#"))
    except OSError:
        return frozenset()


def problems(password: str, username: str = "", full_name: str = "", email: str = "") -> list[str]:
    """Everything wrong with a proposed password, in plain words (empty list = fine)."""
    out: list[str] = []
    p = password or ""
    low = p.lower()
    if len(p) < MIN_LEN:
        out.append(f"Use at least {MIN_LEN} characters (a few words together is easy to remember)")
    if len(p) > MAX_LEN:
        out.append(f"Use at most {MAX_LEN} characters")
    core = re.sub(r"[\d\W_]+$", "", low)  # "password1!" → "password"
    if low in common() or (core and core in common() and len(core) >= 4):
        out.append("That's one of the most common passwords, so it's among the first ones attackers try")
    personal = {username.lower(), email.lower().split("@")[0]} | {w.lower() for w in re.split(r"\s+", full_name or "") if len(w) >= 3}
    personal = {x for x in personal if len(x) >= 3} | {"benchtop"}
    if any(x in low for x in personal):
        out.append("Don't build it from your username, name or email")
    if p and len(set(p)) <= 2:
        out.append("Don't repeat one or two characters")
    elif len(p) >= 6 and any(low in s or low in s[::-1] for s in _SEQUENCES):
        out.append("Don't use a keyboard or number sequence")
    return out


def check(password: str, username: str = "", full_name: str = "", email: str = "") -> None:
    from fastapi import HTTPException
    found = problems(password, username, full_name, email)
    if found:
        raise HTTPException(422, found[0] + ".")
