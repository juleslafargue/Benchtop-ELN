"""DOIs for References sections: tidy what people paste, and look up a paper's details at doi.org.

doi.org hands the request to whoever registered the DOI (Crossref for most journals, DataCite for datasets and
Zenodo, mEDRA, JaLC…), which answers in CSL-JSON, the citation format reference managers use. Redirects are only
followed to those registrars, never to a publisher's web page, so a lab server only ever talks to them.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Optional
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from . import __version__
from .config import settings

BASE = "https://doi.org/"
ACCEPT = "application/vnd.citationstyles.csl+json"
# doi.org itself, and the registrars that answer it with CSL-JSON
REGISTRARS = ("doi.org", "crossref.org", "datacite.org", "crosscite.org", "medra.org", "jalc.org", "japanlinkcenter.org")
MAX_BYTES = 5_000_000  # Crossref includes every reference the paper cites; a big review is a few hundred kB
MAX_REDIRECTS = 5
_transport: Optional[httpx.BaseTransport] = None  # tests inject an httpx.MockTransport

# limits for what a References section stores
MAX_ITEMS = 500
MAX_AUTHORS = 100
FIELDS = {"doi": 300, "title": 1000, "journal": 300, "year": 20, "volume": 40, "issue": 40, "pages": 60, "note": 2000}

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_PREFIX = re.compile(r"^(?:doi:\s*|(?:https?://)?(?:dx\.)?doi\.org/)", re.I)
_TAG = re.compile(r"<[^<>]*>")


class DoiError(Exception):
    """doi.org couldn't be reached or gave nothing usable (as opposed to 'no such DOI')."""


class NotFound(DoiError):
    """No DOI like this is registered."""


def set_transport(t: Optional[httpx.BaseTransport]) -> None:
    global _transport
    _transport = t


def enabled() -> bool:
    return settings.doi_enabled or _transport is not None


def normalise(text: Any) -> str:
    """The DOI in whatever was pasted ("doi:10.…", "https://doi.org/10.…", with a trailing full stop…), or ""."""
    if not isinstance(text, str):
        return ""
    s = text.strip().strip("<>").strip()
    s = _PREFIX.sub("", s).strip()
    if "%" in s:  # copied from an address bar: https://doi.org/10.1002%2Fanie.…
        s = unquote(s)
    s = s.rstrip(".,;")
    if s.endswith(")") and s.count(")") > s.count("("):  # "(see doi:10.1021/xyz)"
        s = s[:-1]
    return s if len(s) <= FIELDS["doi"] and DOI_RE.match(s) else ""


def _text(v: Any, n: int) -> str:
    """Plain text from a CSL field: a string or a list of strings, with markup (<i>, <sub>, MathML) taken out."""
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, str) and x.strip()), "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str):
        return ""
    v = html.unescape(_TAG.sub("", v[:n * 4]))
    return re.sub(r"\s+", " ", v).strip()[:n]


def _person(p: Any) -> str:
    if not isinstance(p, dict):
        return ""
    family, given = _text(p.get("family"), 150), _text(p.get("given"), 150)
    if family and given:
        return f"{family}, {given}"
    return family or given or _text(p.get("literal") or p.get("name"), 300)


def _year(item: dict) -> str:
    for k in ("issued", "published-print", "published-online", "published", "created"):
        d = item.get(k)
        parts = d.get("date-parts") if isinstance(d, dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            y = parts[0][0]
            if isinstance(y, (int, str)) and not isinstance(y, bool) and str(y).strip().isdigit():
                return str(y).strip()
    return ""


def from_csl(item: Any, doi: str = "") -> dict[str, Any]:
    """One reference, in the shape a References section stores, from a CSL-JSON item."""
    if isinstance(item, list):
        item = next((x for x in item if isinstance(x, dict)), {})
    if not isinstance(item, dict):
        raise DoiError("doi.org's answer wasn't a citation")
    names = item.get("author") or item.get("editor")
    people = [a for a in (_person(p) for p in (names if isinstance(names, list) else [])) if a]
    authors = people[:MAX_AUTHORS] + (["et al."] if len(people) > MAX_AUTHORS else [])
    journal = next((j for j in (_text(item.get(k), FIELDS["journal"]) for k in
                                ("container-title-short", "short-container-title", "container-title", "publisher")) if j), "")
    pages = _text(item.get("page"), FIELDS["pages"]) or _text(item.get("article-number"), FIELDS["pages"])
    given = normalise(item.get("DOI"))
    return clean_item({  # registrars change a DOI's capitals (DOIs ignore case): keep it as it was pasted
        "doi": doi if doi and given.lower() in ("", doi.lower()) else given or doi, "title": _text(item.get("title"), FIELDS["title"]), "authors": authors,
        "journal": journal, "year": _year(item), "volume": _text(item.get("volume"), FIELDS["volume"]),
        "issue": _text(item.get("issue"), FIELDS["issue"]), "pages": pages, "note": "",
    })


def lookup(text: str) -> dict[str, Any]:
    """A paper's details from doi.org. NotFound: no such DOI; DoiError: try again later, or type them in."""
    doi = normalise(text)
    if not doi:
        raise ValueError("That doesn't look like a DOI. A DOI starts with 10. and has a slash, for example 10.1021/jacs.9b13580")
    if not enabled():
        raise DoiError("DOI lookups are turned off on this server (BENCHTOP_DOI=off); type the details in by hand")
    url = BASE + quote(doi, safe="/")
    try:
        with httpx.Client(transport=_transport, timeout=12.0, follow_redirects=False,
                          headers={"Accept": ACCEPT, "User-Agent": f"Benchtop-ELN/{__version__} (+https://github.com/juleslafargue/benchtop-eln)"}) as c:
            for _ in range(MAX_REDIRECTS + 1):
                with c.stream("GET", url) as r:
                    if r.is_redirect:
                        nxt = urljoin(url, r.headers.get("location", ""))
                        parts = urlsplit(nxt)
                        host = (parts.hostname or "").lower()
                        if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in REGISTRARS):
                            raise DoiError("Whoever registered this DOI doesn't share the paper's details; type them in by hand")
                        url = nxt
                        continue
                    if r.status_code == 404:
                        raise NotFound(f"No DOI {doi} is registered. Check it for typos")
                    if r.status_code in (204, 406, 415):
                        raise DoiError("Whoever registered this DOI doesn't share the paper's details; type them in by hand")
                    if r.status_code >= 400:
                        raise DoiError(f"doi.org answered with an error (HTTP {r.status_code}); try again later, or type the details in by hand")
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise DoiError("doi.org's answer was too large to read; type the details in by hand")
                    break
            else:
                raise DoiError("doi.org kept redirecting; try again later, or type the details in by hand")
    except httpx.HTTPError as e:
        raise DoiError("Couldn't reach doi.org. Check the internet connection, or type the details in by hand") from e
    try:
        data = json.loads(body)
    except ValueError as e:
        raise DoiError("doi.org's answer wasn't a citation; type the details in by hand") from e
    ref = from_csl(data, doi)
    if not ref["title"] and not ref["authors"]:
        raise DoiError("doi.org had no title or authors for this DOI; type the details in by hand")
    return ref


# ---------------------------------------------------------------- what a References section stores

def _field(v: Any, n: int) -> str:
    """Text as typed (saving happens while people type, so spaces and line breaks stay), only cut to length."""
    if v is None or isinstance(v, (dict, list, bool)):
        return ""
    return str(v)[:n]


def clean_item(it: Any) -> dict[str, Any] | None:
    """One reference as the app writes it: text fields and a list of author names. A DOI is kept as typed;
    it only becomes a link when it is one (see link())."""
    if not isinstance(it, dict):
        return None
    out: dict[str, Any] = {k: _field(it.get(k), n) for k, n in FIELDS.items()}
    for k in ("doi", "title", "journal", "year", "volume", "issue", "pages"):
        out[k] = out[k].replace("\r", " ").replace("\n", " ")  # one-line fields
    raw = it.get("authors")
    if isinstance(raw, str):
        raw = raw.split(";")
    out["authors"] = [a for a in (_field(x, 300).replace("\n", " ").strip() for x in raw) if a][:MAX_AUTHORS + 1] if isinstance(raw, list) else []
    return out


def clean_content(content: dict[str, Any]) -> dict[str, Any]:
    """A blank reference is kept (someone is about to type one in); exports and reports leave blanks out."""
    items = content.get("items") if isinstance(content.get("items"), list) else []
    return {"items": [c for c in (clean_item(it) for it in items[:MAX_ITEMS]) if c is not None]}


def is_blank(it: Any) -> bool:
    """Nothing typed in yet (or not a reference at all)."""
    if not isinstance(it, dict):
        return True
    return not it.get("authors") and not any(isinstance(it.get(k), str) and it[k].strip() for k in FIELDS)


def link(it: dict) -> str:
    """https://doi.org/… for a valid DOI, else ""."""
    d = normalise(it.get("doi"))
    return BASE + quote(d, safe="/:;()[]") if d else ""


def cite(it: dict) -> dict[str, str]:
    """The parts of a citation in the order chemists write them (ACS style):
    Authors. Title. Journal Year, Volume (Issue), Pages."""
    def s(k: str) -> str:
        v = it.get(k)
        return v.strip() if isinstance(v, str) else ""
    authors = [a.strip() for a in it.get("authors") or [] if isinstance(a, str) and a.strip()]
    pages = re.sub(r"(?<=\w)-(?=\w)", "–", s("pages").replace("--", "–"))
    return {"authors": "; ".join(authors), "title": s("title"), "journal": s("journal"), "year": s("year"), "volume": s("volume"),
            "issue": s("issue"), "pages": pages, "doi": normalise(it.get("doi")), "link": link(it), "note": s("note")}


def plain(it: dict) -> str:
    """The whole citation as one line of text (search, and anywhere without formatting)."""
    c = cite(it)
    src = " ".join(x for x in (c["journal"], c["year"]) if x)
    tail = ", ".join(x for x in (src, (c["volume"] + (f" ({c['issue']})" if c["issue"] else "")).strip(), c["pages"]) if x)
    parts = [x for x in (c["authors"], c["title"], tail) if x]
    s = ". ".join(p.rstrip(".") for p in parts) + ("." if parts else "")
    if c["doi"]:
        s += f" DOI: {c['doi']}"
    elif isinstance(it.get("doi"), str) and it["doi"].strip():
        s += f" {it['doi'].strip()}"
    return s.strip()
