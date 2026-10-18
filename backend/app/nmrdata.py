"""Download the current NMRShiftDB2 export and rebuild the shift table on this computer.

    python -m app.nmrdata              # download if NMRShiftDB2 has published something newer
    python -m app.nmrdata --force      # download and rebuild regardless
    python -m app.nmrdata --file x.sd  # build from a file you downloaded yourself

The previous table stays in use until the new one is complete, so a failed or interrupted
update never leaves Benchtop without predictions.
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from . import __version__, nmrpredict
from .config import settings

log = logging.getLogger("benchtop.nmrdata")
URL = "https://sourceforge.net/projects/nmrshiftdb2/files/data/nmrshiftdb2withsignals.sd/download"
STATE = settings.data_dir / "nmr_update.json"
_lock = threading.Lock()
_progress: dict[str, Any] = {"running": False}


def _state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save(**kw: Any) -> None:
    s = {**_state(), **kw}
    STATE.write_text(json.dumps(s, indent=1))


def progress() -> dict[str, Any]:
    return {**_progress, **{k: v for k, v in _state().items() if k in ("last_check", "last_update", "last_error", "remote_modified")}}


def remote_modified(client: httpx.Client) -> Optional[str]:
    """Last-Modified of the export, without downloading it (None if the server doesn't say)."""
    try:
        r = client.head(URL, follow_redirects=True)
        return r.headers.get("last-modified")
    except httpx.HTTPError:
        return None


def download(dest: Path, client: httpx.Client) -> None:
    tmp = dest.with_suffix(".part")
    with client.stream("GET", URL, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
                got += len(chunk)
                _progress.update(stage="downloading", done_mb=round(got / 1e6, 1), total_mb=round(total / 1e6, 1) if total else None)
    head = tmp.open("rb").read(4096)
    if b"<html" in head.lower() or b"<!doctype" in head.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("SourceForge returned a web page instead of the data file; try again later or use --file")
    tmp.replace(dest)


def update(force: bool = False, file: Optional[Path] = None) -> dict[str, Any]:
    """Rebuild the shift table from NMRShiftDB2. Returns a summary; raises on failure."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("An update is already running")
    try:
        _progress.update(running=True, stage="checking", started=datetime.now(timezone.utc).isoformat())
        remote: Optional[str] = None
        work = settings.data_dir / "nmrshiftdb2"
        work.mkdir(parents=True, exist_ok=True)
        if file is None:
            with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), headers={"User-Agent": f"Benchtop-ELN/{__version__}"}) as c:
                remote = remote_modified(c)
                _save(last_check=datetime.now(timezone.utc).isoformat(), remote_modified=remote)
                if not force and remote and remote == _state().get("built_from_modified") and nmrpredict.user_table().exists():
                    return {"updated": False, "reason": "NMRShiftDB2 hasn't published a newer export", "remote_modified": remote}
                file = work / "nmrshiftdb2withsignals.sd"
                download(file, c)
        _progress.update(stage="building", molecules=0)
        src = "NMRShiftDB2 export nmrshiftdb2withsignals.sd" + (f" (published {remote})" if remote else f" ({Path(file).name})")
        res = nmrpredict.build([Path(file)], nmrpredict.user_table(), src, progress=lambda n: _progress.update(molecules=n))
        _save(last_update=datetime.now(timezone.utc).isoformat(), built_from_modified=remote, last_error=None)
        return {"updated": True, **{k: res[k] for k in ("molecules", "assignments_13C", "assignments_1H", "rows")}}
    except Exception as e:  # noqa: BLE001 - reported to the user, old table stays in place
        _save(last_error=f"{datetime.now(timezone.utc):%Y-%m-%d}: {e}")
        raise
    finally:
        _progress.clear()
        _progress["running"] = False
        _lock.release()


def maybe_update() -> Optional[dict[str, Any]]:
    """Housekeeping: at most monthly, rebuild if NMRShiftDB2 published something newer."""
    if not settings.nmr_auto_update:
        return None
    last = _state().get("last_check")
    if last and datetime.fromisoformat(last) > datetime.now(timezone.utc) - timedelta(days=30):
        return None
    try:
        return update()
    except Exception as e:  # noqa: BLE001
        log.warning("NMRShiftDB2 update failed (keeping the current table): %s", e)
        return None


def start_background(force: bool = True) -> bool:
    if _progress.get("running"):
        return False
    threading.Thread(target=lambda: _safe(force), daemon=True, name="nmr-update").start()
    return True


def _safe(force: bool) -> None:
    try:
        update(force=force)
    except Exception as e:  # noqa: BLE001
        log.warning("NMRShiftDB2 update failed: %s", e)


def benchmark(sd: Path) -> dict[str, Any]:
    """Build a throwaway table with 5% of molecules held out (chosen by a stable hash, so runs are
    comparable) and print how well the held-out molecules are predicted."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        print(f"Building from {sd} with 5% of molecules held out (a few minutes)…")
        r = nmrpredict.build([sd], Path(d) / "bench.sqlite", "benchmark", holdout=0.05)
    nmrpredict.reset_cache()
    h = r.get("holdout", {})
    for nuc, unit in (("13C", "ppm"), ("1H", "ppm")):
        x = h.get(nuc)
        if not x:
            continue
        tol = next(k for k in x if k.startswith("within_"))
        print(f"\n{nuc}: mean error {x['mae']} {unit} · median {x['median']} · 90% within {x['p90']} · {x[tol]:.0%} {tol.replace('_', ' ')} · "
              f"{x['coverage']:.1%} of atoms predicted ({x['n']:,} shifts, {x['molecules']:,} molecules)")
        for c, v in x["by_confidence"].items():
            print(f"   {c:7s} confidence  mean {v['mae']:<6} median {v['median']:<6} 90% within {v['p90']:<6} n={v['n']}")
        print("   by kind of atom (worst first):")
        for c, v in x["by_atom"].items():
            print(f"     {c:26s} mean {v['mae']:<6} median {v['median']:<6} n={v['n']}")
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description="Update Benchtop's NMR shift table from NMRShiftDB2")
    ap.add_argument("--force", action="store_true", help="rebuild even if NMRShiftDB2 hasn't changed")
    ap.add_argument("--file", type=Path, help="build from an nmrshiftdb2withsignals.sd you downloaded yourself")
    ap.add_argument("--benchmark", type=Path, metavar="SD_FILE",
                    help="measure prediction accuracy: build from SD_FILE with 5%% of molecules held out and report the errors on them")
    a = ap.parse_args()
    if a.benchmark:
        benchmark(a.benchmark)
        return
    print("Checking NMRShiftDB2…" if not a.file else f"Building from {a.file}…")
    t0 = datetime.now()

    def tick() -> None:
        import time
        while _progress.get("running") is not False:
            st = _progress.get("stage")
            if st == "downloading":
                print(f"\r  downloading {_progress.get('done_mb', 0)} MB" + (f" of {_progress['total_mb']} MB" if _progress.get("total_mb") else ""), end="", flush=True)
            elif st == "building":
                print(f"\r  building: {_progress.get('molecules', 0):,} structures read          ", end="", flush=True)
            time.sleep(1)
    threading.Thread(target=tick, daemon=True).start()
    try:
        r = update(force=a.force, file=a.file)
    except Exception as e:  # noqa: BLE001
        print(f"\nUpdate failed: {e}\nThe previous shift table is still in use.")
        raise SystemExit(1)
    print()
    if not r["updated"]:
        print(r["reason"])
    else:
        print(f"Done in {(datetime.now() - t0).seconds // 60} min: {int(r['molecules']):,} structures, "
              f"{int(r['assignments_13C']):,} 13C and {int(r['assignments_1H']):,} 1H assignments.")


if __name__ == "__main__":
    main()
