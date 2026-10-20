"""0.15: backups keep each attached file once, in a shared `benchtop-files` folder beside the zips."""
import io
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

from app import backup, check
from app.config import settings


def new_exp(client, h, nb, title="Backup files exp"):
    r = client.post("/api/experiments", headers=h, json={"title": title, "notebook_id": nb["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def attach(client, h, exp, name: str, data: bytes):
    r = client.post(f"/api/experiments/{exp['id']}/attachments", headers=h,
                    files={"file": (name, io.BytesIO(data), "application/octet-stream")})
    assert r.status_code == 201, r.text
    return r.json()


def stored(folder: Path, sha: str) -> Path:
    return folder / backup.FILES / sha[:2] / sha


@pytest.fixture()
def bdir(tmp_path, monkeypatch):
    """A backups folder of its own, so counts aren't disturbed by other tests' backups."""
    d = tmp_path / "backups"
    monkeypatch.setattr(settings, "backup_dir", d)
    return d


@pytest.fixture()
def copy_folder(tmp_path):
    d = tmp_path / "offsite" / "Benchtop backups"
    (tmp_path / "offsite").mkdir()
    yield d
    backup.set_copy_dir(None)


def test_each_file_is_kept_once_however_many_backups(client, users, notebook, bdir):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    big = os.urandom(300_000)
    a = attach(client, h, exp, "lcms.zip", big)
    b1 = backup.create_backup("test")
    b2 = backup.create_backup("test")
    # the file is in the shared folder once, and neither zip carries it
    assert stored(bdir, a["sha256"]).read_bytes() == big
    for b in (b1, b2):
        assert b["size"] < 300_000 and b["attachments"] >= 1 and b["format"] == 2
        assert a["sha256"] in backup.read_manifest(Path(b["path"]))["files"]
    assert len(list((bdir / backup.FILES).rglob(a["sha256"]))) == 1
    # a new file adds only itself
    a2 = attach(client, h, exp, "second.bin", b"second file")
    before = {p.name for p in (bdir / backup.FILES).rglob("*") if p.is_file()}
    backup.create_backup("test")
    after = {p.name for p in (bdir / backup.FILES).rglob("*") if p.is_file()}
    assert after - before == {a2["sha256"]}
    st = backup.storage()
    assert st["files"] >= 2 and st["bytes"] >= 300_000


def test_second_copy_gets_the_files_first_and_tests_cleanly(client, users, admin, notebook, bdir, copy_folder):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    a = attach(client, h, exp, "nmr.zip", b"bruker data " * 1000)
    assert client.put("/api/admin/backups/copy-dir", headers=admin, json={"path": str(copy_folder)}).status_code == 200
    b = client.post("/api/admin/backups", headers=admin).json()
    assert b["copy"]["ok"], b["copy"]
    assert (copy_folder / b["name"]).exists() and stored(copy_folder, a["sha256"]).is_file()
    t = client.post("/api/admin/backups/test", headers=admin).json()
    assert t["ok"] and t["where"] == "second copy", t
    # the next backup copies nothing new
    b2 = client.post("/api/admin/backups", headers=admin).json()
    assert b2["copy"]["ok"] and b2["copy"]["new_files"] == 0
    info = client.get("/api/admin/backups", headers=admin).json()
    assert info["files"]["files"] >= 1


def test_zip_isnt_copied_without_its_files(client, users, notebook, bdir, copy_folder):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    a = attach(client, h, exp, "x.bin", b"needs to travel with the zip")
    backup.set_copy_dir(str(copy_folder))
    backup.set_copy_dir(None)  # make the backup without a second place…
    b = backup.create_backup("test")
    stored(bdir, a["sha256"]).unlink()  # …lose a file locally…
    backup.set_copy_dir(str(copy_folder))
    res = backup.copy_to_second_place(Path(b["path"]))  # …and the copy refuses rather than leave a zip without it
    assert not res["ok"] and "missing" in res["error"]
    assert not (copy_folder / b["name"]).exists()


def test_backup_test_spots_missing_and_damaged_shared_files(client, users, notebook, bdir):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    a = attach(client, h, exp, "spectrum.jdx", b"##TITLE=damage me\n##END=\n")
    b = backup.create_backup("test")
    assert check.check_backup(Path(b["path"]))["ok"]
    p = stored(bdir, a["sha256"])
    p.write_bytes(b"##TITLE=changed!!\n##END=\n")
    res = check.check_backup(Path(b["path"]))
    assert not res["ok"] and any(c["name"] == "Attached files" and "changed" in c["detail"] for c in res["checks"])
    p.unlink()
    res = check.check_backup(Path(b["path"]))
    assert not res["ok"] and any(c["name"] == "Shared file folder" and not c["ok"] for c in res["checks"])


def test_restore_refuses_without_the_shared_folder_and_changes_nothing(client, users, notebook, bdir, tmp_path):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    attach(client, h, exp, "only-here.bin", b"only in the shared folder")
    b = backup.create_backup("test")
    lonely = tmp_path / "elsewhere" / b["name"]  # someone copied just the zip
    lonely.parent.mkdir()
    shutil.copy(b["path"], lonely)
    db = backup._sqlite_path()
    before = db.stat().st_mtime
    with pytest.raises(backup.BackupError, match="Copy the whole backups folder") as e:
        backup.restore(str(lonely))
    assert "Nothing was changed" in str(e.value)
    assert db.stat().st_mtime == before and not list(settings.data_dir.glob(".restore-*"))


def test_restore_without_missing_files_when_asked(client, users, notebook, bdir, tmp_path):
    h = users["alice"]
    exp = new_exp(client, h, notebook, title="Partly restorable")
    a = attach(client, h, exp, "gone.bin", b"this one is lost")
    b = backup.create_backup("test")
    stored(bdir, a["sha256"]).unlink()
    aside, problems = backup._restore(b["path"], allow_missing=True)
    assert aside.is_dir() and problems and "missing" in problems[0]
    titles = [e["title"] for e in client.get("/api/experiments", headers=h).json()]
    assert "Partly restorable" in titles
    assert client.get(f"/api/attachments/{a['id']}", headers=h).status_code == 410
    live = settings.attachments_dir / a["sha256"][:2] / a["sha256"]  # put it back for the other tests
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes(b"this one is lost")


def test_backups_from_before_015_still_restore_and_test(client, users, notebook, bdir, tmp_path):
    """An old-style backup carries every file inside the zip and has no "format" or "files"."""
    h = users["alice"]
    exp = new_exp(client, h, notebook, title="From an old backup")
    a = attach(client, h, exp, "old.jdx", b"##TITLE=old style\n##END=\n")
    b = backup.create_backup("test")
    old = tmp_path / "old-style" / "benchtop-backup-20250101-000000.zip"
    old.parent.mkdir()
    with zipfile.ZipFile(b["path"]) as zin, zipfile.ZipFile(old, "w") as zout:
        m = json.loads(zin.read("manifest.json"))
        for k in ("format", "files", "attachment_bytes"):
            m.pop(k, None)
        zout.writestr("manifest.json", json.dumps(m))
        zout.writestr("benchtop.db", zin.read("benchtop.db"))
        for sha in backup.read_manifest(Path(b["path"]))["files"]:
            zout.write(stored(bdir, sha), f"attachments/{sha[:2]}/{sha}")
    assert check.check_backup(old)["ok"]
    client.post("/api/experiments", headers=h, json={"title": "After the old backup", "notebook_id": notebook["id"]})
    backup.restore(str(old))
    titles = [e["title"] for e in client.get("/api/experiments", headers=h).json()]
    assert "From an old backup" in titles and "After the old backup" not in titles
    assert client.get(f"/api/attachments/{a['id']}", headers=h).content == b"##TITLE=old style\n##END=\n"


def test_an_unsafe_backup_is_refused_before_anything_moves(client, tmp_path, bdir):
    b = backup.create_backup("test")
    evil = bdir / "benchtop-backup-20000101-000000.zip"  # beside its files, so only the bad path is wrong
    with zipfile.ZipFile(b["path"]) as zin, zipfile.ZipFile(evil, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("attachments/../../escaped.txt", b"nope")
    db = backup._sqlite_path()
    before = db.stat().st_mtime
    with pytest.raises(backup.BackupError, match="unsafe path"):
        backup.restore(str(evil))
    assert db.stat().st_mtime == before
    assert not list(settings.data_dir.glob("pre-restore-*/benchtop.db")) or all(
        p.stat().st_mtime < before + 1 for p in settings.data_dir.glob("pre-restore-*/benchtop.db"))
    assert not (settings.data_dir.parent / "escaped.txt").exists()


def test_a_file_damaged_later_keeps_its_good_backup_copy(client, users, notebook, bdir):
    h = users["alice"]
    exp = new_exp(client, h, notebook, title="Bit rot")
    a = attach(client, h, exp, "precious.jdx", b"##TITLE=precious\n##END=\n")
    backup.create_backup("test")
    live = settings.attachments_dir / a["sha256"][:2] / a["sha256"]
    live.write_bytes(b"##TITLE=rotten!!\n##END=\n")  # the live copy goes bad
    b = backup.create_backup("test")  # (files already kept aren't re-read daily; ./benchtop check spots the damage)
    assert stored(bdir, a["sha256"]).read_bytes() == b"##TITLE=precious\n##END=\n"  # never overwritten
    backup.restore(b["path"])
    assert client.get(f"/api/attachments/{a['id']}", headers=h).content == b"##TITLE=precious\n##END=\n"


def test_a_damaged_file_with_no_good_copy_still_travels_in_the_zip(client, users, notebook, bdir):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    a = attach(client, h, exp, "never-backed-up.bin", b"original content")
    live = settings.attachments_dir / a["sha256"][:2] / a["sha256"]
    live.write_bytes(b"changed content!")
    b = backup.create_backup("test")
    assert a["sha256"] in b["damaged"] and not stored(bdir, a["sha256"]).exists()
    with zipfile.ZipFile(b["path"]) as z:
        assert f"attachments/{a['sha256'][:2]}/{a['sha256']}" in z.namelist()
    res = check.check_backup(Path(b["path"]))
    assert any(c["name"] == "Attached files" and "changed" in c["detail"] for c in res["checks"])
    live.write_bytes(b"original content")  # put it back for the other tests


def test_cleanup_only_removes_unneeded_old_files(client, users, notebook, bdir):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    a = attach(client, h, exp, "kept.bin", b"needed by a backup")
    backup.create_backup("test")
    store = bdir / backup.FILES
    old = time.time() - 3 * 86400
    orphan = store / "ff" / ("f" * 64)  # not needed by any backup, not in Benchtop's data, old
    young = store / "ee" / ("e" * 64)  # not needed, but new: a backup in another process may be writing it
    stale_part = store / "dd" / (".x.part")
    for p in (orphan, young, stale_part):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    os.utime(orphan, (old, old))
    os.utime(stale_part, (old, old))
    os.utime(stored(bdir, a["sha256"]), (old, old))
    backup.prune()
    assert not orphan.exists() and not stale_part.exists()
    assert young.exists() and stored(bdir, a["sha256"]).exists()


def test_cleanup_does_nothing_if_a_backup_cant_be_read(client, users, notebook, bdir):
    backup.create_backup("test")
    (bdir / "benchtop-backup-20000101-000000.zip").write_bytes(b"not a zip")
    orphan = bdir / backup.FILES / "ff" / ("f" * 64)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"x")
    old = time.time() - 3 * 86400
    os.utime(orphan, (old, old))
    backup.prune()
    assert orphan.exists()


def test_two_backups_in_the_same_second_sort_newest_first(tmp_path, monkeypatch):
    """Regression: plain name order put …-120000.zip before …-120000-2.zip, so pruning kept the older one."""
    monkeypatch.setattr(settings, "backup_dir", tmp_path)
    for n in ("benchtop-backup-20260101-120000.zip", "benchtop-backup-20260101-120000-2.zip",
              "benchtop-backup-20260101-120000-10.zip", "benchtop-backup-20251231-235959.zip"):
        (tmp_path / n).write_bytes(b"x")
    assert [b["name"] for b in backup.list_backups()] == [
        "benchtop-backup-20260101-120000-10.zip", "benchtop-backup-20260101-120000-2.zip",
        "benchtop-backup-20260101-120000.zip", "benchtop-backup-20251231-235959.zip"]
    backup.prune(keep=2)
    assert sorted(p.name for p in tmp_path.glob("*.zip")) == [
        "benchtop-backup-20260101-120000-10.zip", "benchtop-backup-20260101-120000-2.zip"]


def test_older_benchtop_would_refuse_these_backups(client, bdir):
    """0.14.x refuses a backup whose schema_version is above 2, before touching anything; 0.15 backups say 3."""
    from app import migrations
    b = backup.create_backup("test")
    assert migrations.SCHEMA_VERSION >= 3 and backup.read_manifest(Path(b["path"]))["schema_version"] >= 3
