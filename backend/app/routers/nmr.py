"""NMR shift prediction (NMRShiftDB2) and its data updates."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import nmrdata, nmrpredict
from ..security import Admin, CurrentUser

router = APIRouter(prefix="/api/nmr", tags=["nmr prediction"])


@router.get("/status")
def nmr_status(_: CurrentUser):
    return nmrpredict.status()


@router.get("/predict")
def nmr_predict(smiles: str, _: CurrentUser, nucleus: str = "1H"):
    try:
        return nmrpredict.predict(smiles, nucleus)
    except nmrpredict.NotReady as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/update", status_code=202)
def nmr_update(_: Admin):
    """Download the latest NMRShiftDB2 export and rebuild the table in the background."""
    if not nmrdata.start_background(force=True):
        raise HTTPException(409, "An update is already running")
    import time
    time.sleep(0.2)  # let the worker report its first stage
    return nmrpredict.status()
