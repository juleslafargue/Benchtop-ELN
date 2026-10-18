"""Database models.

Domain overview (mirrors the publicly documented concepts of commercial chemistry ELNs):

    Notebook ─┬─ Experiment ─┬─ Entry (text | reaction | table | file)  ── EntryVersion (full history)
              │              ├─ Attachment (content-addressed, sha256)
              │              └─ Signature (author / witness, bound to a content hash)
    Material ─┴─ Lot (container) ── LotTransaction (usage ledger, optionally linked to an experiment)
    AuditEvent: append-only, SHA-256 hash-chained log of every mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import NaiveDatetime
from sqlalchemy import JSON, BigInteger, Column, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, Enum):
    admin = "admin"
    scientist = "scientist"
    viewer = "viewer"


class ExperimentStatus(str, Enum):
    open = "open"            # editable
    signed = "signed"        # author signed; content locked, awaiting witness
    witnessed = "witnessed"  # countersigned; final
    voided = "voided"        # withdrawn (content kept, flagged)


class EntryKind(str, Enum):
    text = "text"
    reaction = "reaction"
    table = "table"
    file = "file"
    purification = "purification"
    characterization = "characterization"
    safety = "safety"
    screen = "screen"
    custom = "custom"  # a section type described by a plugin file


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str = ""
    email: str = ""
    password_hash: str
    role: Role = Role.scientist
    active: bool = True
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    # added in 0.4 (nullable so existing databases can be upgraded in place)
    preferences: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    # added in 0.9.1: an admin reset the password, so the person must choose their own at next sign-in
    must_change_password: Optional[bool] = Field(default=None, nullable=True)
    password_changed_at: Optional[NaiveDatetime] = Field(default=None, nullable=True)


class AuthSession(SQLModel, table=True):
    """One signed-in browser. Tokens name their session, so a session can be ended from anywhere."""
    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    last_seen_at: NaiveDatetime = Field(default_factory=utcnow)
    expires_at: NaiveDatetime
    method: str = "password"  # password | sso | setup
    user_agent: str = ""
    ip: str = ""
    revoked_at: Optional[NaiveDatetime] = None


class AuthFailure(SQLModel, table=True):
    """A failed password check (sign-in, signature, password change), for throttling guessing."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True)
    at: NaiveDatetime = Field(default_factory=utcnow, index=True)


class UserIdentity(SQLModel, table=True):
    """A sign-in from an identity provider (single sign-on) linked to a Benchtop account."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    issuer: str = Field(index=True)
    subject: str = Field(index=True)
    email: str = ""
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    last_login_at: Optional[NaiveDatetime] = None


class Notebook(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str = ""
    owner_id: int = Field(foreign_key="user.id")
    archived: bool = False
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    # added in 0.9: who can see it. None/"everyone" = every account (how notebooks worked before groups),
    # "group" = members of group_id, "private" = its owner (plus the group's leads, if it has a group)
    group_id: Optional[int] = Field(default=None, foreign_key="labgroup.id", nullable=True)
    visibility: Optional[str] = Field(default=None, nullable=True)


class LabGroup(SQLModel, table=True):
    """A research group or team. Leads (PIs, managers) see every notebook in the group."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class GroupMember(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="labgroup.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str = "member"  # member | lead
    added_at: NaiveDatetime = Field(default_factory=utcnow)


class ExperimentShare(SQLModel, table=True):
    """One experiment shared with one person outside the notebook's audience."""
    id: Optional[int] = Field(default=None, primary_key=True)
    experiment_id: int = Field(foreign_key="experiment.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    permission: str = "view"  # view | edit
    created_by: int = Field(foreign_key="user.id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Comment(SQLModel, table=True):
    """Discussion on an experiment or one of its sections. Not part of the signed record."""
    id: Optional[int] = Field(default=None, primary_key=True)
    experiment_id: int = Field(foreign_key="experiment.id", index=True)
    entry_id: Optional[int] = Field(default=None, foreign_key="entry.id", nullable=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="comment.id", nullable=True)
    user_id: int = Field(foreign_key="user.id")
    body: str = Field(default="", sa_column=Column(Text))
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    edited_at: Optional[NaiveDatetime] = None
    deleted: bool = False
    resolved_at: Optional[NaiveDatetime] = None
    resolved_by: Optional[int] = Field(default=None, foreign_key="user.id", nullable=True)


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str  # mention | reply | comment | share | review
    actor_id: Optional[int] = Field(default=None, foreign_key="user.id", nullable=True)
    experiment_id: Optional[int] = Field(default=None, foreign_key="experiment.id", nullable=True)
    comment_id: Optional[int] = Field(default=None, foreign_key="comment.id", nullable=True)
    text: str = ""
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    read_at: Optional[NaiveDatetime] = None


class Experiment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    notebook_id: Optional[int] = Field(default=None, foreign_key="notebook.id", index=True)
    title: str
    description: str = ""
    status: ExperimentStatus = ExperimentStatus.open
    owner_id: int = Field(foreign_key="user.id")
    is_template: bool = False
    template_id: Optional[int] = Field(default=None, foreign_key="experiment.id")
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)

    @property
    def eid(self) -> str:
        prefix = "TPL" if self.is_template else "EXP"
        return f"{prefix}-{self.id:05d}"


class Entry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    experiment_id: int = Field(foreign_key="experiment.id", index=True)
    kind: EntryKind
    title: str = ""
    position: int = 0
    content: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    version: int = 1
    deleted: bool = False
    updated_by: Optional[int] = Field(default=None, foreign_key="user.id")
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class EntryVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    entry_id: int = Field(foreign_key="entry.id", index=True)
    version: int
    title: str = ""
    content: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    deleted: bool = False
    created_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Attachment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    experiment_id: int = Field(foreign_key="experiment.id", index=True)
    filename: str
    content_type: str = "application/octet-stream"
    size: int = 0
    sha256: str = Field(index=True)
    uploaded_by: int = Field(foreign_key="user.id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Signature(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    experiment_id: int = Field(foreign_key="experiment.id", index=True)
    user_id: int = Field(foreign_key="user.id")
    meaning: str  # "author" | "witness" | "void"
    comment: str = ""
    content_hash: str
    signed_at: NaiveDatetime = Field(default_factory=utcnow)
    # the signer's printed name and username as they were at signing (21 CFR 11.50); added in 0.4
    signer_name: Optional[str] = Field(default=None, nullable=True)
    signer_username: Optional[str] = Field(default=None, nullable=True)


class Material(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    kind: str = "reagent"  # reagent | solvent | sample | product | other
    smiles: str = ""
    cas: str = Field(default="", index=True)
    formula: str = ""
    mw: Optional[float] = None
    inchikey: str = Field(default="", index=True)
    density: Optional[float] = None  # g/mL
    synonyms: str = ""
    hazards: str = ""
    pubchem_cid: Optional[int] = None
    notes: str = Field(default="", sa_column=Column(Text))
    source_experiment_id: Optional[int] = Field(default=None, foreign_key="experiment.id")
    created_by: int = Field(foreign_key="user.id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)

    @property
    def mid(self) -> str:
        return f"MAT-{self.id:05d}"


class Lot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: int = Field(foreign_key="material.id", index=True)
    barcode: str = Field(index=True)
    supplier: str = ""
    catalog_number: str = ""
    purity: Optional[float] = None  # %
    amount: float = 0.0
    unit: str = "g"  # g | mg | kg | mL | L | uL
    location: str = ""
    status: str = "available"  # available | empty | disposed | quarantined
    received_at: NaiveDatetime = Field(default_factory=utcnow)
    expires_at: Optional[NaiveDatetime] = None
    created_by: int = Field(foreign_key="user.id")


class LotTransaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    lot_id: int = Field(foreign_key="lot.id", index=True)
    delta: float  # in the lot's unit; negative = consumed
    balance_after: float
    experiment_id: Optional[int] = Field(default=None, foreign_key="experiment.id")
    note: str = ""
    user_id: int = Field(foreign_key="user.id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class AuditEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: NaiveDatetime = Field(default_factory=utcnow, index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    username: str = ""
    action: str = Field(index=True)
    object_type: str = Field(index=True)
    object_id: Optional[int] = Field(default=None, index=True)
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    prev_hash: str = ""
    hash: str = ""


class ChemSafety(SQLModel, table=True):
    """GHS data for one chemical as last fetched from PubChem, with provenance and change tracking.
    Keyed by InChIKey when there is a structure, otherwise 'cas:<CAS>' or 'name:<lowercase name>'."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    cid: Optional[int] = None
    title: str = ""
    data: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    status: str = "pending"  # ok | unclassified | not_found | error | pending
    error: str = ""
    fetched_at: Optional[NaiveDatetime] = None  # last successful fetch
    checked_at: Optional[NaiveDatetime] = None  # last attempt (successful or not)
    changed_at: Optional[NaiveDatetime] = None  # last time the classification changed between fetches
    change: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    acknowledged_at: Optional[NaiveDatetime] = None


class ApiToken(SQLModel, table=True):
    """A personal access token for scripts and other programs. Only its SHA-256 is stored."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = ""
    token_hash: str = Field(index=True, unique=True)
    prefix: str = ""  # first characters, so people can tell tokens apart
    scope: str = "read"  # read | write
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    expires_at: Optional[NaiveDatetime] = None
    last_used_at: Optional[NaiveDatetime] = None
    last_used_ip: str = ""
    revoked_at: Optional[NaiveDatetime] = None


class Webhook(SQLModel, table=True):
    """An address Benchtop calls when something happens (signed, witnessed, commented…)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    url: str
    secret: str = ""
    events: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    format: str = "json"  # json | slack | teams
    include_private: bool = False
    active: bool = True
    created_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class WebhookDelivery(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    webhook_id: int = Field(foreign_key="webhook.id", index=True)
    event: str = ""
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    status_code: Optional[int] = None
    ok: bool = False
    error: str = ""
    attempts: int = 0
    duration_ms: Optional[int] = None


class PluginState(SQLModel, table=True):
    """Which plugin files an admin has switched on."""
    plugin_id: str = Field(primary_key=True)
    enabled: bool = False
    changed_by: Optional[int] = Field(default=None, foreign_key="user.id")
    changed_at: NaiveDatetime = Field(default_factory=utcnow)


class LabShift(SQLModel, table=True):
    """A confirmed chemical shift from a signed experiment, keyed by the atom's environment code, so
    predictions learn from the lab's own assigned spectra."""
    id: Optional[int] = Field(default=None, primary_key=True)
    experiment_id: int = Field(foreign_key="experiment.id", index=True)
    nuc: int = Field(index=True)
    r: int
    key: int = Field(sa_column=Column(BigInteger, index=True))
    shift: float
    solvent: str = ""
    created_at: NaiveDatetime = Field(default_factory=utcnow)
