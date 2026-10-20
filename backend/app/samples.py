"""Sample codes: JL-013 instead of EXP-00042 in an instrument's sample name.

Each person chooses a prefix once (their initials, say JL). On an experiment, "New sample code" hands out
their next number (JL-013) and links it to that experiment. When an instrument file's name, folder or title
carries JL-013 (JL-13, jl_013 and "JL 013" too; a date after it is fine), Benchtop knows where it belongs.
A code with a known prefix but a number never handed out goes to that person's Inbox instead: Benchtop
never guesses which of their experiments it was.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from .models import SampleCode, User

PREFIX = re.compile(r"^[A-Z][A-Z0-9]{1,5}$")
RESERVED = {"EXP", "TPL"}  # experiment and template numbers


def fmt(prefix: str, number: int) -> str:
    return f"{prefix}-{number:03d}"


def clean_prefix(raw: str) -> str:
    p = (raw or "").strip().upper()
    if not PREFIX.match(p):
        raise ValueError("Use 2–6 letters or digits, starting with a letter: your initials, like JL or JLA")
    if p in RESERVED:
        raise ValueError(f"{p} is used for Benchtop's own numbers; choose something else, like your initials")
    return p


@dataclass
class Found:
    prefix: str
    number: int
    code: Optional[SampleCode]  # None: a number this prefix never handed out
    owner_id: Optional[int]

    @property
    def text(self) -> str:
        return fmt(self.prefix, self.number)


def find(session: Session, *texts: str) -> list[Found]:
    """Sample codes in these texts, each once, in the order they appear."""
    owners = {u.sample_prefix: u.id for u in session.exec(select(User).where(User.sample_prefix != None))}  # noqa: E711
    prefixes = set(owners) | set(session.exec(select(SampleCode.prefix).distinct()))
    if not prefixes:
        return []
    alt = "|".join(re.escape(p) for p in sorted(prefixes, key=len, reverse=True))
    pat = re.compile(rf"(?<![A-Za-z0-9])({alt})[-_ ](\d{{1,6}})(?!\d)", re.IGNORECASE)
    out: list[Found] = []
    seen: set[tuple[str, int]] = set()
    for t in texts:
        for m in pat.finditer(t or ""):
            key = (m.group(1).upper(), int(m.group(2)))
            if key in seen:
                continue
            seen.add(key)
            sc = session.exec(select(SampleCode).where(SampleCode.prefix == key[0], SampleCode.number == key[1])).first()
            live = sc if sc and sc.retired_at is None else None  # a retired code points nowhere: its owner sorts it out
            out.append(Found(key[0], key[1], live, sc.user_id if sc else owners.get(key[0])))
    return out


def next_number(session: Session, prefix: str) -> int:
    last = session.exec(select(SampleCode.number).where(SampleCode.prefix == prefix).order_by(SampleCode.number.desc())).first()
    return (last or 0) + 1


def out(session: Session, sc: SampleCode) -> dict:
    from .services import user_brief
    return {"id": sc.id, "code": fmt(sc.prefix, sc.number), "user": user_brief(session, sc.user_id),
            "created_at": sc.created_at.isoformat() + "Z"}
