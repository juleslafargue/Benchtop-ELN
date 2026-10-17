"""Everything the dashboard widgets show, in one request."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from sqlmodel import select

from ..models import Attachment, Experiment, ExperimentStatus, Lot, LotTransaction, Material, Signature, utcnow
from ..security import DB, CurrentUser
from ..services import experiment_summary
from .reactions import _collect

router = APIRouter(prefix="/api", tags=["dashboard"])

SPECTRUM_RE = re.compile(r"\.(jdx|dx|jcamp|jcm|csv|tsv|xy|zip)$", re.I)
LOW_STOCK_FRACTION = 0.2
EXPIRY_DAYS = 30


@router.get("/dashboard")
def dashboard(user: CurrentUser, session: DB):
    exps = session.exec(select(Experiment).where(Experiment.is_template == False)  # noqa: E712
                        .order_by(Experiment.updated_at.desc())).all()
    mine = [e for e in exps if e.owner_id == user.id]
    summ = lambda e: experiment_summary(session, e)  # noqa: E731

    # returned to me: open, mine, and the latest signature event is a rejection
    returned = []
    for e in mine:
        if e.status != ExperimentStatus.open:
            continue
        last = session.exec(select(Signature).where(Signature.experiment_id == e.id).order_by(Signature.id.desc())).first()
        if last and last.meaning == "rejected":
            returned.append({**summ(e), "reason": last.comment, "returned_by": last.signer_name})

    pinned_ids = [i for i in ((user.preferences or {}).get("pinned") or []) if isinstance(i, int)]
    by_id = {e.id: e for e in exps}
    pinned = [summ(by_id[i]) for i in pinned_ids if i in by_id]

    # lab stats (everyone's reactions)
    rx = _collect(session)
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    def created(e: Experiment) -> Any:
        return e.created_at
    this_month = sum(1 for e in exps if created(e) >= month_start)
    last_month = sum(1 for e in exps if prev_start <= created(e) < month_start)
    witnessed_month = sum(1 for e in exps if e.status == ExperimentStatus.witnessed and e.updated_at >= month_start)
    yields = [{"experiment_id": r["experiment_id"], "eid": r["eid"], "title": r["title"], "date": r["date"],
               "yield_pct": r["yield_pct"], "product": r["product"]} for r in rx if r["yield_pct"] is not None]

    spectra = []
    for a, e in session.exec(select(Attachment, Experiment).join(Experiment, Experiment.id == Attachment.experiment_id)
                             .order_by(Attachment.created_at.desc())).all():
        if SPECTRUM_RE.search(a.filename):
            spectra.append({"id": a.id, "filename": a.filename, "experiment_id": e.id, "eid": e.eid, "title": e.title,
                            "created_at": a.created_at.isoformat() + "Z"})
        if len(spectra) >= 10:
            break

    # inventory alerts
    low, expiring = [], []
    for lot, m in session.exec(select(Lot, Material).join(Material, Material.id == Lot.material_id)
                               .where(Lot.status == "available")).all():
        first = session.exec(select(LotTransaction).where(LotTransaction.lot_id == lot.id).order_by(LotTransaction.id)).first()
        initial = first.balance_after if first and first.delta > 0 else None
        row = {"lot_id": lot.id, "barcode": lot.barcode, "material_id": m.id, "material": m.name,
               "amount": lot.amount, "unit": lot.unit, "location": lot.location}
        if initial and lot.amount < LOW_STOCK_FRACTION * initial:
            low.append({**row, "fraction": round(lot.amount / initial, 3)})
        if lot.expires_at and lot.expires_at <= now + timedelta(days=EXPIRY_DAYS):
            expiring.append({**row, "expires_at": lot.expires_at.isoformat() + "Z", "expired": lot.expires_at <= now})
    low.sort(key=lambda r: r["fraction"])
    expiring.sort(key=lambda r: r["expires_at"])

    return {
        "lists": {
            "recent": [summ(e) for e in mine[:8]],
            "open": [summ(e) for e in mine if e.status == ExperimentStatus.open][:10],
            "awaiting": [summ(e) for e in exps if e.status == ExperimentStatus.signed and e.owner_id != user.id][:10],
            "returned": returned[:10],
            "pinned": pinned,
        },
        "stats": {"this_month": this_month, "last_month": last_month, "witnessed_this_month": witnessed_month,
                  "yields": yields[:200]},
        "spectra": spectra,
        "inventory": {"low": low[:10], "expiring": expiring[:10], "low_fraction": LOW_STOCK_FRACTION, "expiry_days": EXPIRY_DAYS},
    }
