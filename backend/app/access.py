"""Who can see and change what.

Visibility is decided per notebook, with per-experiment shares on top:

* **everyone** (the default, and how every notebook behaved before groups): every account can read it.
* **group**: members of the notebook's group can read it.
* **private**: only its owner, plus the leads of its group if it has one.

Group leads (a PI, a lab manager) can read every notebook in their group, private ones included.
An experiment can also be shared with individual people, to view or to edit. Templates stay
visible to everyone. Admins see everything.

Editing is narrower than reading: the experiment's owner, an admin, or someone it was shared with
for editing. Signing stays with the author.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlmodel import Session, false, or_, select

from .models import Experiment, ExperimentShare, GroupMember, Notebook, Role, User

VISIBILITIES = ("everyone", "group", "private")


@dataclass
class Access:
    user: User
    all: bool = False
    notebooks: set[int] = field(default_factory=set)       # readable notebook ids
    groups: dict[int, str] = field(default_factory=dict)   # group id -> my role there
    shares: dict[int, str] = field(default_factory=dict)   # experiment id -> view | edit

    # ------------------------------------------------------------ reading
    def can_read_notebook(self, nb: Notebook) -> bool:
        return self.all or nb.id in self.notebooks

    def can_read(self, exp: Experiment) -> bool:
        if self.all or exp.owner_id == self.user.id or exp.is_template:
            return True
        return exp.notebook_id in self.notebooks or exp.id in self.shares

    def filter(self, stmt):
        """Restrict a select(Experiment ...) statement to what this user can read."""
        if self.all:
            return stmt
        conds = [Experiment.owner_id == self.user.id, Experiment.is_template == True]  # noqa: E712
        if self.notebooks:
            conds.append(Experiment.notebook_id.in_(self.notebooks))
        if self.shares:
            conds.append(Experiment.id.in_(self.shares))
        return stmt.where(or_(*conds))

    def filter_notebooks(self, stmt):
        if self.all:
            return stmt
        return stmt.where(Notebook.id.in_(self.notebooks)) if self.notebooks else stmt.where(false())

    # ------------------------------------------------------------ writing
    def can_edit(self, exp: Experiment) -> bool:
        if self.user.role == Role.viewer:
            return False
        return (self.user.role == Role.admin or exp.owner_id == self.user.id or exp.is_template
                or self.shares.get(exp.id) == "edit")

    def can_manage_notebook(self, nb: Notebook) -> bool:
        return self.user.role == Role.admin or nb.owner_id == self.user.id

    def leads(self, group_id: int | None) -> bool:
        return group_id is not None and self.groups.get(group_id) == "lead"


def access_for(session: Session, user: User) -> Access:
    """Computed once per request and kept on the user object."""
    cached = getattr(user, "_access", None)
    if cached is not None:
        return cached
    a = Access(user=user, all=user.role == Role.admin)
    if not a.all:
        a.groups = {m.group_id: m.role for m in session.exec(select(GroupMember).where(GroupMember.user_id == user.id))}
        for nb in session.exec(select(Notebook)):
            vis = nb.visibility or "everyone"
            if (vis == "everyone" or nb.owner_id == user.id
                    or (vis == "group" and nb.group_id in a.groups)
                    or a.groups.get(nb.group_id) == "lead"):
                a.notebooks.add(nb.id)
        a.shares = {s.experiment_id: s.permission for s in session.exec(select(ExperimentShare).where(ExperimentShare.user_id == user.id))}
    object.__setattr__(user, "_access", a)
    return a


def require_read(session: Session, user: User, exp: Experiment) -> None:
    # 404, not 403: don't confirm that a record someone can't see exists
    if not access_for(session, user).can_read(exp):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Experiment {exp.id} not found")


def visible_experiment_filter(stmt, session: Session, user: User):
    return access_for(session, user).filter(stmt)
