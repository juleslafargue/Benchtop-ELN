"""PubChem client: find compounds and read their GHS classification.

PubChem aggregates GHS data from official sources (EU CLP Annex VI harmonised classification,
ECHA C&L Inventory notifications, Japan NITE, Safe Work Australia HCIS, HSDB, ...).
We pick one *primary* source per compound (harmonised > largest ECHA aggregate > others),
keep its statements, and list anything other sources add, so nothing is silently dropped.

Usage policy: PubChem asks for no more than 5 requests/second; we stay under 4.
"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import quote

import httpx

from . import __version__
from .config import settings

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest"
_transport: Optional[httpx.BaseTransport] = None  # tests inject an httpx.MockTransport
_lock = threading.Lock()
_last_call = 0.0
MIN_INTERVAL = 0.26


class PubChemError(Exception):
    """Network or service problem (as opposed to 'not found')."""


def set_transport(t: Optional[httpx.BaseTransport]) -> None:
    global _transport
    _transport = t


def enabled() -> bool:
    return settings.pubchem_enabled or _transport is not None


def _get(path: str, params: dict[str, str] | None = None) -> Optional[dict[str, Any]]:
    """GET a PubChem JSON resource. None means 'not found'; PubChemError means try again later."""
    global _last_call
    if not enabled():
        raise PubChemError("PubChem lookups are turned off (BENCHTOP_PUBCHEM=off)")
    with _lock:  # serialise and pace requests
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    try:
        with httpx.Client(transport=_transport, timeout=12.0, follow_redirects=True,
                          headers={"User-Agent": f"Benchtop-ELN/{__version__}"}) as c:
            r = c.get(BASE + path, params=params)
    except httpx.HTTPError as e:
        raise PubChemError(f"Couldn't reach PubChem ({e.__class__.__name__})") from e
    if r.status_code in (400, 404):
        return None
    if r.status_code == 503:
        raise PubChemError("PubChem is busy; will retry later")
    if r.status_code >= 400:
        raise PubChemError(f"PubChem returned HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise PubChemError("PubChem returned something that isn't JSON") from e


# ---------------------------------------------------------------- finding compounds

CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def valid_cas(s: str) -> bool:
    if not CAS_RE.match(s or ""):
        return False
    digits = s.replace("-", "")
    body, check = digits[:-1], int(digits[-1])
    return sum(int(d) * (i + 1) for i, d in enumerate(reversed(body))) % 10 == check


def _cids(d: Optional[dict[str, Any]]) -> list[int]:
    return list(((d or {}).get("IdentifierList") or {}).get("CID") or [])


def find_cid(*, inchikey: str = "", cas: str = "", name: str = "", smiles: str = "") -> Optional[int]:
    """Most specific identifier first. Returns None if PubChem has no match."""
    if inchikey:
        c = _cids(_get(f"/pug/compound/inchikey/{quote(inchikey)}/cids/JSON"))
        if c:
            return c[0]
    for q in (cas, name):
        if q:
            c = _cids(_get(f"/pug/compound/name/{quote(q, safe='')}/cids/JSON"))
            if c:
                return c[0]
    if smiles:
        c = _cids(_get("/pug/compound/smiles/cids/JSON", {"smiles": smiles}))
        if c and c[0]:
            return c[0]
    return None


def compound(cid: int) -> dict[str, Any]:
    """Name, structure, formula, MW, InChIKey, CAS and (best effort) density."""
    p = _get(f"/pug/compound/cid/{cid}/property/Title,IsomericSMILES,MolecularFormula,MolecularWeight,InChIKey/JSON")
    props = (((p or {}).get("PropertyTable") or {}).get("Properties") or [{}])[0]
    smiles = next((props[k] for k in ("SMILES", "IsomericSMILES", "CanonicalSMILES", "ConnectivitySMILES") if props.get(k)), "")
    out: dict[str, Any] = {
        "cid": cid, "name": props.get("Title", ""), "smiles": smiles, "formula": props.get("MolecularFormula", ""),
        "mw": float(props["MolecularWeight"]) if props.get("MolecularWeight") else None,
        "inchikey": props.get("InChIKey", ""), "cas": "", "density": None,
        "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
    }
    syn = _get(f"/pug/compound/cid/{cid}/synonyms/JSON")
    names = ((((syn or {}).get("InformationList") or {}).get("Information") or [{}])[0]).get("Synonym") or []
    out["cas"] = next((s for s in names if valid_cas(s)), "")
    try:
        out["density"] = parse_density(_get(f"/pug_view/data/compound/{cid}/JSON", {"heading": "Density"}))
    except PubChemError:
        pass
    return out


def _sections(node: dict[str, Any], heading: str) -> list[dict[str, Any]]:
    found = []
    for s in node.get("Section") or []:
        if s.get("TOCHeading") == heading:
            found.append(s)
        found.extend(_sections(s, heading))
    return found


def _strings(info: dict[str, Any]) -> list[dict[str, Any]]:
    v = info.get("Value") or {}
    return v.get("StringWithMarkup") or []


def parse_density(d: Optional[dict[str, Any]]) -> Optional[float]:
    """First plausible g/mL value from the free-text Density section (liquids and solids)."""
    rec = (d or {}).get("Record") or {}
    for sec in _sections(rec, "Density"):
        for info in sec.get("Information") or []:
            for sw in _strings(info):
                s = sw.get("String", "")
                if re.search(r"vapou?r|relative to air|air\s*=", s, re.I):
                    continue
                m = re.search(r"(\d+(?:\.\d+)?)\s*(?:g/(?:cu\s*)?cm|g/mL|g/ml|g cm|g/cm3|g/cm³)", s, re.I) \
                    or re.match(r"^\s*(\d\.\d+)\b", s)
                if m:
                    v = float(m.group(1))
                    if 0.3 <= v <= 25:
                        return round(v, 4)
    return None


# ---------------------------------------------------------------- GHS

H_RE = re.compile(r"^\s*((?:EU)?H\d{3}[A-Za-z]*(?:\s*\+\s*(?:EU)?H\d{3}[A-Za-z]*)*)\s*(?:\(([^)]*)\))?\s*:\s*(.*?)\s*(?:\[(.*)\])?\s*$")
P_RE = re.compile(r"P\d{3}(?:\+P\d{3})*")
PICTO_NAMES = {
    "GHS01": "Explosive", "GHS02": "Flammable", "GHS03": "Oxidizer", "GHS04": "Compressed Gas",
    "GHS05": "Corrosive", "GHS06": "Acute Toxic", "GHS07": "Irritant", "GHS08": "Health Hazard", "GHS09": "Environmental Hazard",
}


def _pct(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
    return float(m.group(1)) if m else None


def _source_rank(ref: dict[str, Any], group: dict[str, Any]) -> tuple:
    name = (ref.get("SourceName") or "")
    if "1272/2008" in name:  # EU CLP Annex VI: legally binding harmonised classification
        return (3, 0)
    if "ECHA" in name or "European Chemicals Agency" in name:
        return (2, group.get("reports") or 0)
    return (1, 0)


def parse_ghs(d: Optional[dict[str, Any]], cid: int | None = None) -> Optional[dict[str, Any]]:
    """Structured GHS data from a PUG View 'GHS Classification' record, or None if there is none."""
    rec = (d or {}).get("Record") or {}
    secs = _sections(rec, "GHS Classification")
    if not secs:
        return None
    refs = {r.get("ReferenceNumber"): r for r in rec.get("Reference") or []}
    groups: dict[Any, dict[str, Any]] = defaultdict(lambda: {"pictograms": [], "signal": "", "hazards": [], "precautions": [],
                                                             "reports": None, "not_classified": False})
    for sec in secs:
        for info in sec.get("Information") or []:
            g = groups[info.get("ReferenceNumber")]
            name = info.get("Name", "")
            for sw in _strings(info):
                s = (sw.get("String") or "").strip()
                if name == "Pictogram(s)":
                    for m in sw.get("Markup") or []:
                        code = re.search(r"GHS0\d", m.get("URL", "") or "")
                        if code and code.group(0) not in [p["code"] for p in g["pictograms"]]:
                            g["pictograms"].append({"code": code.group(0), "name": m.get("Extra") or PICTO_NAMES.get(code.group(0), "")})
                elif name == "Signal":
                    if s:
                        g["signal"] = s
                elif name == "GHS Hazard Statements":
                    m = H_RE.match(s)
                    if m:
                        g["hazards"].append({"code": re.sub(r"\s+", "", m.group(1)), "pct": _pct(m.group(2)),
                                             "text": m.group(3), "category": m.group(4) or ""})
                    elif re.search(r"not (?:meet|classified)", s, re.I):
                        g["not_classified"] = True
                elif name == "Precautionary Statement Codes":
                    for code in P_RE.findall(s):
                        if code not in g["precautions"]:
                            g["precautions"].append(code)
                elif name == "ECHA C&L Notifications Summary":
                    m = re.search(r"per ([\d,]+) reports", s) or re.search(r"provided by ([\d,]+) compan", s)
                    if m:
                        g["reports"] = int(m.group(1).replace(",", ""))
                    if re.search(r"not meeting GHS hazard criteria", s, re.I) and not g["hazards"]:
                        g["not_classified"] = True
    usable = {k: g for k, g in groups.items() if g["hazards"] or g["signal"] or g["not_classified"]}
    if not usable:
        return None
    primary_ref = max(usable, key=lambda k: _source_rank(refs.get(k, {}), usable[k]))
    g, ref = usable[primary_ref], refs.get(primary_ref, {})
    hazards = g["hazards"]
    codes = {h["code"] for h in hazards}
    pictos = g["pictograms"] or pictograms_for([h["code"] for h in hazards])

    # statements other sources add that the primary doesn't carry
    others: dict[str, dict[str, Any]] = {}
    for k, og in usable.items():
        if k == primary_ref:
            continue
        sname = (refs.get(k, {}).get("SourceName") or "").replace("Regulation (EC) No 1272/2008 of the European Parliament and of the Council", "EU CLP (harmonised)")
        for h in og["hazards"]:
            if h["code"] in codes:
                continue
            o = others.setdefault(h["code"], {"code": h["code"], "text": h["text"], "sources": [], "max_pct": None})
            if sname and sname not in o["sources"]:
                o["sources"].append(sname)
            if h["pct"] is not None:
                o["max_pct"] = max(o["max_pct"] or 0, h["pct"])
    source_label = ref.get("SourceName", "")
    if "1272/2008" in source_label:
        source_label = "EU CLP harmonised classification (Annex VI)"
    elif g["reports"]:
        source_label = f"ECHA C&L Inventory (aggregated from {g['reports']:,} company reports)"
    data = {
        "cid": cid, "title": rec.get("RecordTitle", ""),
        "source": {"name": source_label, "ref_name": ref.get("Name", ""), "url": ref.get("URL", ""), "reports": g["reports"]},
        "signal": g["signal"] or ("" if not hazards else "Warning"),
        "pictograms": pictos, "hazards": hazards, "precautions": g["precautions"],
        "other_hazards": sorted(others.values(), key=lambda o: o["code"]),
        "not_classified": bool(g["not_classified"] and not hazards),
        "sources": sorted({(refs.get(k, {}).get("SourceName") or "") for k in usable} - {""}),
        "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}#section=Safety-and-Hazards" if cid else "",
    }
    data["flags"] = flags_for(effective_codes(data))
    return data


def ghs(cid: int) -> Optional[dict[str, Any]]:
    return parse_ghs(_get(f"/pug_view/data/compound/{cid}/JSON", {"heading": "GHS Classification"}), cid)


# ---------------------------------------------------------------- interpreting codes

def split_codes(code: str) -> list[str]:
    return [c for c in code.replace(" ", "").split("+") if c]


def effective_codes(data: dict[str, Any] | None) -> set[str]:
    """Codes used for warnings: the primary source's, plus anything another source reports with
    at least 10% of notifiers (or from a source that doesn't give a share). Errs on the side of caution."""
    if not data:
        return set()
    out = {c for h in data.get("hazards") or [] for c in split_codes(h["code"])}
    for o in data.get("other_hazards") or []:
        if o.get("max_pct") is None or o["max_pct"] >= 10:
            out.update(split_codes(o["code"]))
    return out


FLAGS: list[tuple[str, str, tuple[str, ...]]] = [
    ("explosive", "Explosive", ("H200", "H201", "H202", "H203", "H204", "H205", "H240", "H241")),
    ("pyrophoric", "Pyrophoric (ignites in air)", ("H250",)),
    ("water_reactive", "Reacts with water", ("H260", "H261", "EUH014", "EUH029")),
    ("peroxide", "May form explosive peroxides", ("EUH019",)),
    ("oxidiser", "Oxidiser", ("H270", "H271", "H272")),
    ("flammable", "Flammable", ("H220", "H221", "H222", "H223", "H224", "H225", "H226", "H228", "H229", "H230", "H231", "H232", "H242")),
    ("acute_fatal", "Fatal on exposure", ("H300", "H310", "H330")),
    ("toxic", "Toxic", ("H301", "H311", "H331")),
    ("corrosive", "Corrosive to skin or eyes", ("H314", "H318")),
    ("cmr", "Carcinogen, mutagen or reproductive toxin", ("H340", "H350", "H360")),
    ("cmr_suspected", "Suspected carcinogen, mutagen or reproductive toxin", ("H341", "H351", "H361")),
    ("sensitiser", "Sensitiser", ("H317", "H334")),
    ("organ", "Causes organ damage", ("H370", "H372")),
    ("acid_gas", "Releases toxic gas with acids", ("EUH031", "EUH032")),
    ("aquatic", "Toxic to aquatic life", ("H400", "H410", "H411")),
]


def flags_for(codes: set[str]) -> list[dict[str, str]]:
    out = []
    for key, label, prefixes in FLAGS:
        hit = sorted(c for c in codes if any(c == p or (c.startswith(p) and not c[len(p):].isdigit()) for p in prefixes))
        if hit:
            out.append({"key": key, "label": label, "codes": hit})
    return out


_PICTO_FOR = [
    ("GHS01", ("H200", "H201", "H202", "H203", "H204", "H205", "H240", "H241")),
    ("GHS02", ("H220", "H221", "H222", "H223", "H224", "H225", "H226", "H228", "H241", "H242", "H250", "H251", "H252", "H260", "H261")),
    ("GHS03", ("H270", "H271", "H272")),
    ("GHS04", ("H280", "H281")),
    ("GHS05", ("H290", "H314", "H318")),
    ("GHS06", ("H300", "H301", "H310", "H311", "H330", "H331")),
    ("GHS07", ("H302", "H312", "H315", "H317", "H319", "H332", "H335", "H336")),
    ("GHS08", ("H304", "H334", "H340", "H341", "H350", "H351", "H360", "H361", "H370", "H371", "H372", "H373")),
    ("GHS09", ("H400", "H410", "H411")),
]


def pictograms_for(codes: list[str]) -> list[dict[str, str]]:
    cs = {c for code in codes for c in split_codes(code)}
    return [{"code": p, "name": PICTO_NAMES[p]} for p, hs in _PICTO_FOR
            if any(c[:4] in hs for c in cs)]
