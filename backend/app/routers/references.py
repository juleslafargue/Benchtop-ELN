"""DOI lookup for References sections (the section's content itself is saved like any other)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import doi
from ..security import Writer

router = APIRouter(prefix="/api", tags=["references"])


@router.get("/doi")
def lookup_doi(q: str, _: Writer):
    """A paper's title, authors, journal, year, volume, issue and pages, from doi.org."""
    try:
        return doi.lookup(q)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except doi.NotFound as e:
        raise HTTPException(404, str(e)) from None
    except doi.DoiError as e:
        raise HTTPException(503 if not doi.enabled() else 502, str(e)) from None
