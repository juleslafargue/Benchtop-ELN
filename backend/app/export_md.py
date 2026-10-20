"""Markdown / Obsidian export.

Each experiment becomes `<vault>/<Notebook>/<EID> <Title>.md` with YAML front matter that
Dataview can query (yield, conditions, status, tags...). Reaction schemes are written as SVG
and attachments are copied to `<vault>/_assets/`, both embedded with Obsidian `![[...]]` links.

Sync a vault folder (safe to repeat; it rewrites only Benchtop's own notes):
    python -m app.export_md ~/Documents/MyVault/Benchtop
or set BENCHTOP_OBSIDIAN_DIR and the server syncs hourly.
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from . import chem
from .config import settings
from .models import Attachment, EntryKind, Experiment, Notebook, User
from .services import content_hash, live_entries, signatures_out

PURIF = {"column": "Flash column chromatography", "prep_tlc": "Preparative TLC", "prep_hplc": "Preparative HPLC",
         "recrystallization": "Recrystallisation", "trituration": "Trituration", "distillation": "Distillation",
         "extraction": "Extraction only", "none": "Used crude"}


# ------------------------------------------------------------------ HTML -> Markdown (the subset our editor produces)

class _MD(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.lists: list[list[Any]] = []  # [kind, counter]
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None

    def _w(self, s: str) -> None:
        (self.cell if self.cell is not None else self.out).append(s)

    def handle_starttag(self, tag, attrs):
        if tag in ("strong", "b"):
            self._w("**")
        elif tag in ("em", "i"):
            self._w("*")
        elif tag in ("sub", "sup"):
            self._w(f"<{tag}>")
        elif tag in ("h1", "h2", "h3", "h4"):
            self._w("\n\n" + "#" * (int(tag[1]) + 1) + " ")
        elif tag == "p" and self.cell is None:
            self._w("\n\n" if not self.lists else "")
        elif tag == "br":
            self._w("  \n")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
            self._w("\n")
        elif tag == "li":
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            indent = "   " * (len(self.lists) - 1)
            self._w(f"\n{indent}{kind[1]}. " if kind[0] == "ol" else f"\n{indent}- ")
        elif tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.table.append([])
        elif tag in ("td", "th") and self.table is not None:
            self.cell = []
        elif tag == "code":
            self._w("`")

    def handle_endtag(self, tag):
        if tag in ("strong", "b"):
            self._w("**")
        elif tag in ("em", "i"):
            self._w("*")
        elif tag in ("sub", "sup"):
            self._w(f"</{tag}>")
        elif tag in ("ul", "ol") and self.lists:
            self.lists.pop()
            self._w("\n")
        elif tag in ("td", "th") and self.table is not None and self.cell is not None:
            self.table[-1].append("".join(self.cell).strip().replace("|", "\\|").replace("\n", " "))
            self.cell = None
        elif tag == "table" and self.table is not None:
            rows = [r for r in self.table if r]
            self.table = None
            if rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                md = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self.out.append("\n\n" + "\n".join(md) + "\n")
        elif tag == "code":
            self._w("`")

    def handle_data(self, data):
        if self.table is not None and self.cell is None:
            return
        self._w(data)


def html_to_md(src: str) -> str:
    p = _MD()
    p.feed(src or "")
    text = "".join(p.out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ------------------------------------------------------------------ one experiment

def _yaml(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(round(v, 4)) if isinstance(v, float) else str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_yaml(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)  # JSON strings are valid YAML


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, float):
        return f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return str(v)


def safe_name(s: str, limit: int = 80) -> str:
    s = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", s).strip()
    return re.sub(r"\s+", " ", s)[:limit].rstrip(" .") or "untitled"


def experiment_markdown(session: Session, exp: Experiment, asset_dir: str = "_assets",
                        assets: dict[str, bytes] | None = None, base_url: str = "http://localhost:8000") -> str:
    """Render one experiment. If `assets` is given, files to write alongside are added to it."""
    nb = session.get(Notebook, exp.notebook_id) if exp.notebook_id else None
    owner = session.get(User, exp.owner_id)
    entries = live_entries(session, exp.id)
    atts = {a.id: a for a in session.exec(select(Attachment).where(Attachment.experiment_id == exp.id,
                                                                   Attachment.withdrawn_at == None))}  # noqa: E711
    rxn = next((e for e in entries if e.kind == EntryKind.reaction and (e.content or {}).get("rows")), None)
    pur = next((e.content for e in entries if e.kind == EntryKind.purification), None) or {}
    fm: dict[str, Any] = {
        "type": "experiment", "eid": exp.eid, "title": exp.title, "status": exp.status.value,
        "notebook": nb.name if nb else None, "author": (owner.full_name or owner.username) if owner else None,
        "created": exp.created_at.strftime("%Y-%m-%d"), "updated": exp.updated_at.strftime("%Y-%m-%d"),
        "tags": [re.sub(r"\s+", "-", t) for t in (exp.tags or [])],
    }
    try:  # front-matter summary; an odd old record just leaves these fields out
        if rxn:
            c = rxn.content
            cond = c.get("conditions") or {}
            prods = [r for r in c.get("rows", []) if r.get("role") == "product"]
            best = max(prods, key=lambda p: p.get("yield_pct") or -1) if prods else None
            lim = next((r for r in c.get("rows", []) if r.get("limiting")), None)
            fm.update({
                "reaction_smiles": c.get("reaction_smiles") or None,
                "limiting_reagent": (lim.get("name") or lim.get("formula")) if lim else None,
                "scale_mmol": (c.get("totals") or {}).get("limiting_mmol"),
                "temperature_c": cond.get("temperature_c"), "temperature": cond.get("temperature_label") or None,
                "time_h": cond.get("time_h"), "atmosphere": cond.get("atmosphere") or None,
                "concentration_m": (c.get("totals") or {}).get("concentration_m"),
                "product": (best.get("name") or best.get("formula")) if best else None,
                "yield_pct": best.get("yield_pct") if best else None,
            })
    except (AttributeError, TypeError, ValueError):
        pass
    if isinstance(pur, dict) and isinstance(pur.get("method"), str):
        fm["purification"] = pur.get("method")
    fm["benchtop"] = f"{base_url}/experiments/{exp.id}"
    fm["content_hash"] = content_hash(session, exp)

    dates = {"created", "updated"}  # unquoted ISO dates so Dataview sorts and filters them as dates
    out = ["---"] + [f"{k}: {v if k in dates else _yaml(v)}" for k, v in fm.items() if v not in (None, [], "")] + ["---", "", f"# {exp.title}", ""]
    meta = f"**{exp.eid}** · {exp.status.value} · {fm.get('author') or ''}"
    out += [meta, ""]
    if exp.description:
        out += [exp.description, ""]

    for e in entries:
        mark = len(out)
        try:
            c = e.content or {}
            out += [f"## {e.title}", ""]
            if e.kind == EntryKind.text:
                out += [html_to_md(c.get("html", "")) or "_(empty)_", ""]
            elif e.kind == EntryKind.reaction:
                cond = c.get("conditions") or {}
                bits = [x for x in (
                    f"{_fmt(cond.get('temperature_c'))} °C" if cond.get("temperature_c") is not None else None,
                    cond.get("temperature_label"), f"{_fmt(cond.get('time_h'))} h" if cond.get("time_h") is not None else None,
                    cond.get("atmosphere"), f"{_fmt(cond.get('target_conc_m'))} M" if cond.get("target_conc_m") else None) if x]
                if bits:
                    out += [f"**Conditions:** {', '.join(bits)}" + (f". {cond['notes']}" if cond.get("notes") else ""), ""]
                if c.get("reaction_smiles") and assets is not None:
                    try:
                        svg = chem.depict_reaction_svg(c["reaction_smiles"], 700, 190)
                        name = f"{exp.eid}-scheme-{e.id}.svg"
                        assets[name] = svg.encode()
                        out += [f"![[{name}]]", ""]
                    except chem.ChemError:
                        pass
                rows = c.get("rows") or []
                if rows:
                    out += ["| Role | Name | MW | Equiv | mmol | Mass (mg) | Vol (mL) | Yield |", "|---|---|---|---|---|---|---|---|"]
                    for r in rows:
                        out.append("| " + " | ".join([
                            r.get("role", "") + (" ★" if r.get("limiting") else ""), (r.get("name") or r.get("formula") or "").replace("|", "/"),
                            _fmt(r.get("mw"), 2), _fmt(r.get("equiv")), _fmt(r.get("mmol"), 4),
                            _fmt(r.get("actual_mass_mg") if r.get("role") == "product" else r.get("mass_mg"), 2),
                            _fmt(r.get("volume_ml"), 3), f"{_fmt(r.get('yield_pct'), 1)}%" if r.get("yield_pct") is not None else ""]) + " |")
                    out.append("")
            elif e.kind == EntryKind.safety:
                snap = c.get("snapshot") or {}
                for ch in snap.get("chemicals") or []:
                    out.append(f"- **{ch.get('name', '')}**" + (f" ({ch['signal']})" if ch.get("signal") else "") + ": " + "; ".join(ch.get("hazards") or []))
                for w in snap.get("warnings") or []:
                    out.append(f"> [!warning] {w.get('text', '')}")
                for k, label in (("controls", "Controls"), ("waste", "Waste"), ("emergency", "Emergency")):
                    if c.get(k):
                        out.append(f"- **{label}:** {c[k]}")
                notes = html_to_md(c.get("notes_html", ""))
                out += ([notes] if notes else []) + [""]
            elif e.kind == EntryKind.screen:
                xs, ys, cells = c.get("x_levels") or [], c.get("y_levels") or [], c.get("cells") or {}
                if xs and ys:
                    out += [f"{c.get('response', '')}: {c.get('y_name', '')} (rows) × {c.get('x_name', '')} (columns)", "",
                            "| | " + " | ".join(xs) + " |", "|---|" + "---|" * len(xs)]
                    out += ["| " + yv + " | " + " | ".join(_fmt((cells.get(f"{i},{j}") or {}).get("value"), 1) for j in range(len(xs))) + " |"
                            for i, yv in enumerate(ys)]
                    out.append("")
            elif e.kind == EntryKind.purification:
                items = [("Method", PURIF.get(c.get("method"), c.get("method"))), ("Stationary phase", c.get("stationary_phase")),
                         ("Eluent", c.get("eluent")), ("Rf", (c.get("rf") or "") + (f" ({c['rf_eluent']})" if c.get("rf_eluent") else "")),
                         ("Isolated", f"{_fmt(c.get('isolated_mg'), 2)} mg" if c.get("isolated_mg") is not None else None),
                         ("Appearance", c.get("appearance"))]
                out += [f"- **{k}:** {v}" for k, v in items if v] + [""]
                notes = html_to_md(c.get("notes_html", ""))
                if notes:
                    out += [notes, ""]
            elif e.kind == EntryKind.characterization:
                for it in c.get("items", []):
                    if not (it.get("data") or it.get("attachment_id")):
                        continue
                    line = f"- **{it.get('technique', '')}**" + (f" ({it['conditions']})" if it.get("conditions") else "")
                    line += f" {it.get('data', '')}".rstrip()
                    a = atts.get(it.get("attachment_id"))
                    if a:
                        line += f" — [[{_asset_name(a)}]]"
                        if assets is not None:
                            assets[_asset_name(a)] = _blob(a)
                    out.append(line)
                out.append("")
            elif e.kind == EntryKind.table:
                cols = c.get("columns") or []
                if cols:
                    out += ["| " + " | ".join(str(col.get("name", "") if isinstance(col, dict) else col) for col in cols) + " |", "|" + "---|" * len(cols)]
                    out += ["| " + " | ".join(str(v).replace("|", "/") for v in r) + " |" for r in c.get("rows") or [] if isinstance(r, list)]
                    out.append("")
            elif e.kind == EntryKind.custom:
                from .plugins import text_of
                out += [f"- {line}" for line in text_of(c).splitlines()] + [""]
            elif e.kind == EntryKind.file:
                for i in c.get("attachment_ids", []):
                    a = atts.get(i)
                    if not a:
                        continue
                    embed = "!" if a.content_type.startswith("image/") else ""
                    out.append(f"- {embed}[[{_asset_name(a)}]]")
                    if assets is not None:
                        assets[_asset_name(a)] = _blob(a)
                if c.get("caption"):
                    out += ["", str(c["caption"])]
                out.append("")
        except Exception:  # one odd section shouldn't stop the whole export
            del out[mark:]
            out += [f"## {e.title}", "", "_(This section couldn't be exported.)_", ""]

    sigs = signatures_out(session, exp.id)
    if sigs:
        out += ["## Signatures", ""]
        out += [f"- {s['meaning']}: {s['signer_name'] or s['user']['username']}, {s['signed_at'][:16].replace('T', ' ')} UTC"
                + (f" — “{s['comment']}”" if s["comment"] else "") for s in sigs]
        out.append("")
    out += ["---", f"*Exported from Benchtop. The signed record of truth is [{exp.eid} in Benchtop]({fm['benchtop']}); edits made here are not synced back.*", ""]
    return "\n".join(map(str, out))


def _asset_name(a: Attachment) -> str:
    return f"{a.sha256[:8]}-{safe_name(a.filename, 100)}"


def _blob(a: Attachment) -> bytes:
    return (settings.attachments_dir / a.sha256[:2] / a.sha256).read_bytes()


# ------------------------------------------------------------------ vault sync

INDEX = """---
type: benchtop-index
---
# Benchtop experiments

Notes in this folder are generated by Benchtop and rewritten on each sync; keep your own notes elsewhere
and link to these.

```dataview
TABLE eid AS "ID", status, product, yield_pct AS "Yield %", temperature_c AS "°C", time_h AS "h", updated
FROM "{folder}"
WHERE type = "experiment"
SORT updated DESC
```
"""


def sync_vault(session: Session, target: Path, base_url: str = "http://localhost:8000") -> dict[str, int]:
    target = target.expanduser().resolve()
    assets_dir = target / "_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    written = removed = 0
    exps = session.exec(select(Experiment).where(Experiment.is_template == False)).all()  # noqa: E712
    for exp in exps:
        nb = session.get(Notebook, exp.notebook_id) if exp.notebook_id else None
        folder = target / safe_name(nb.name if nb else "No notebook")
        folder.mkdir(parents=True, exist_ok=True)
        assets: dict[str, bytes] = {}
        md = experiment_markdown(session, exp, assets=assets, base_url=base_url)
        path = folder / f"{exp.eid} {safe_name(exp.title)}.md"
        # drop older copies of this experiment (renamed, or moved between notebooks)
        for old in target.rglob(f"{exp.eid} *.md"):
            if old != path and old.is_file():
                old.unlink()
                removed += 1
        if not path.exists() or path.read_text(encoding="utf-8") != md:
            path.write_text(md, encoding="utf-8")
            written += 1
        for name, data in assets.items():
            p = assets_dir / name
            if not p.exists() or p.stat().st_size != len(data):
                p.write_bytes(data)
    (target / "Benchtop index.md").write_text(INDEX.replace("{folder}", target.name), encoding="utf-8")
    return {"experiments": len(exps), "written": written, "removed": removed}


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    from .db import engine, init_db

    init_db()
    with Session(engine) as s:
        r = sync_vault(s, Path(argv[1]))
    print(f"Synced {r['experiments']} experiments to {Path(argv[1]).expanduser()} "
          f"({r['written']} updated, {r['removed']} stale notes removed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
