from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from .. import chem
from ..security import CurrentUser

router = APIRouter(prefix="/api/chem", tags=["chemistry"])


class StructureIn(BaseModel):
    structure: str  # SMILES, molfile, or InChI


@router.post("/properties")
def properties(body: StructureIn, _: CurrentUser):
    try:
        return chem.properties(body.structure)
    except chem.ChemError as exc:
        raise HTTPException(422, str(exc))


@router.get("/depict")
def depict(smiles: str, w: int = 300, h: int = 200, rxn: bool = False):
    """Unauthenticated so it can be used directly in <img src>; it only renders the input."""
    try:
        svg = chem.depict_reaction_svg(smiles, w, h) if rxn else chem.depict_svg(smiles, w, h)
    except chem.ChemError as exc:
        raise HTTPException(422, str(exc))
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


class ReactionIn(BaseModel):
    reaction: str  # reaction SMILES or RXN block


@router.post("/reaction/parse")
def parse_reaction(body: ReactionIn, _: CurrentUser):
    try:
        return chem.parse_reaction(body.reaction)
    except chem.ChemError as exc:
        raise HTTPException(422, str(exc))


class StoichIn(BaseModel):
    rows: list[dict[str, Any]]
    target_conc_m: float | None = None


@router.post("/stoichiometry")
def stoichiometry(body: StoichIn, _: CurrentUser):
    return chem.compute_stoichiometry(body.rows, body.target_conc_m)


@router.get("/structure-check")
def structure_check(smiles: str, _: CurrentUser):
    try:
        return chem.structure_check(smiles)
    except chem.ChemError as exc:
        raise HTTPException(422, str(exc))


@router.get("/hrms")
def hrms(smiles: str, _: CurrentUser):
    try:
        return chem.hrms_ions(smiles)
    except chem.ChemError as exc:
        raise HTTPException(422, str(exc))
