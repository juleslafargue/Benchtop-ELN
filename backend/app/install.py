"""Swap one version of Benchtop's program files for another, for ./benchtop update, upgrade and rollback.

    python -m app.install mirror NEW ROOT     put the version unpacked in NEW into ROOT
    python -m app.install snapshot ROOT DEST  copy ROOT's program files into DEST (for ./benchtop rollback)

Your data is never touched: the data and backup folders, settings, the Python environment, node_modules and Git's
files are always left alone (wherever BENCHTOP_DATA_DIR and BENCHTOP_BACKUP_DIR point, too). Files the new version
no longer has are removed only inside Benchtop's own folders (backend, frontend, docs, deploy, plugins, samples),
so anything else you keep in the Benchtop folder stays. Each file is written beside the old one and then renamed
into place, so even the launcher that's running this is replaced cleanly. Standard library only, on purpose.
"""
from __future__ import annotations

import filecmp
import os
import shutil
import sys
from pathlib import Path

KEEP = (".git", ".github", ".benchtop-previous", "backend/data", "backend/.venv", "frontend/node_modules",
        "deploy/.env", "deploy/backups", "deploy/backup-copy")
PROGRAM_DIRS = ("backend", "frontend", "docs", "deploy", "plugins", "samples")
SKIP_NAMES = ("__pycache__", ".DS_Store", ".pytest_cache")


def _protected(root: Path) -> list[Path]:
    """Folders that hold data, wherever they are set to be (they may be inside the Benchtop folder)."""
    out = []
    for var in ("BENCHTOP_DATA_DIR", "BENCHTOP_BACKUP_DIR", "BENCHTOP_OBSIDIAN_DIR"):
        v = os.environ.get(var)
        if v:
            out.append(Path(v).expanduser().resolve())
    return out + [(root / k).resolve() for k in KEEP]


class _Rules:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.protected = _protected(self.root)

    def kept(self, rel: str) -> bool:
        if any(rel == k or rel.startswith(k + "/") for k in KEEP):
            return True
        p = (self.root / rel).resolve()
        return any(p == q or q in p.parents for q in self.protected)

    def contains_protected(self, rel: str) -> bool:
        p = (self.root / rel).resolve()
        return any(p in q.parents for q in self.protected)


def _place(src: Path, dst: Path) -> bool:
    """Copy one file into place (atomically). Returns True if it changed anything."""
    if dst.is_symlink() or dst.is_dir():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    elif dst.is_file() and not src.is_symlink() and filecmp.cmp(src, dst, shallow=False):
        shutil.copystat(src, dst)  # same content; keep the release's dates so "is the interface built?" stays right
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".benchtop-new")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    if src.is_symlink():
        os.symlink(os.readlink(src), tmp)
    else:
        shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    return True


def mirror(new: Path, root: Path) -> dict[str, int]:
    new, root = new.resolve(), root.resolve()
    rules = _Rules(root)
    changed = removed = 0
    for dirpath, dirnames, filenames in os.walk(new):
        rel_dir = os.path.relpath(dirpath, new)
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES and not rules.kept(os.path.join(rel_dir, d))]
        for f in filenames:
            rel = os.path.join(rel_dir, f)
            if f in SKIP_NAMES or rules.kept(rel):
                continue
            target = root / rel
            if target.parent.is_file() or target.parent.is_symlink():
                target.parent.unlink()
            changed += _place(new / rel, target)
    # leftovers of the old version, only inside Benchtop's own folders
    for top in PROGRAM_DIRS:
        base = root / top
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            rel_dir = os.path.relpath(dirpath, root)
            keep_dirs = []
            for d in dirnames:
                rel = os.path.join(rel_dir, d)
                if rules.kept(rel) or rules.contains_protected(rel):
                    continue
                if not (new / rel).is_dir() or (root / rel).is_symlink():
                    if not (root / rel).is_symlink():
                        shutil.rmtree(root / rel)
                    else:
                        (root / rel).unlink()
                    removed += 1
                else:
                    keep_dirs.append(d)
            dirnames[:] = keep_dirs
            for f in filenames:
                rel = os.path.join(rel_dir, f)
                if rules.kept(rel):
                    continue
                if not os.path.lexists(new / rel):
                    (root / rel).unlink()
                    removed += 1
    return {"changed": changed, "removed": removed}


def snapshot(root: Path, dest: Path) -> int:
    """Copy the program files (top-level files and Benchtop's own folders) of ROOT into DEST."""
    root = root.resolve()
    rules = _Rules(root)
    n = 0
    for entry in sorted(root.iterdir()):
        rel = entry.name
        if rules.kept(rel) or entry.name in SKIP_NAMES:
            continue
        if entry.is_file() or entry.is_symlink():
            n += _place(entry, dest / rel)
        elif entry.is_dir() and rel in PROGRAM_DIRS:
            for dirpath, dirnames, filenames in os.walk(entry):
                rel_dir = os.path.relpath(dirpath, root)
                dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES and not rules.kept(os.path.join(rel_dir, d))]
                for f in filenames:
                    r = os.path.join(rel_dir, f)
                    if f not in SKIP_NAMES and not rules.kept(r):
                        n += _place(root / r, dest / r)
    return n


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "mirror":
        r = mirror(Path(argv[2]), Path(argv[3]))
        print(f"{r['changed']} files updated, {r['removed']} old ones removed")
        return 0
    if len(argv) == 4 and argv[1] == "snapshot":
        snapshot(Path(argv[2]), Path(argv[3]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
