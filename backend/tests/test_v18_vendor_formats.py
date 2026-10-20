"""0.18: LC-MS/HPLC/GC vendor data, Agilent NMR, and large files without filling memory."""
import io
import os
import tracemalloc
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import export_md, instruments

T0 = datetime(2032, 1, 1, 9, 0, 0)


def scan(seconds: float):
    return instruments.scan_all(now=T0 + timedelta(seconds=seconds))


@pytest.fixture()
def watch(client, admin, tmp_path):
    made = []

    def _watch(kind: str):
        d = tmp_path / kind
        d.mkdir()
        r = client.post("/api/instruments", headers=admin, json={"name": f"Vendor {kind}", "kind": kind, "folder": str(d)})
        assert r.status_code == 201, r.text
        made.append(r.json()["id"])
        scan(0)
        return d
    yield _watch
    for i in made:
        client.delete(f"/api/instruments/{i}", headers=admin)


def new_exp(client, h, nb):
    r = client.post("/api/experiments", headers=h, json={"title": "Vendor data exp", "notebook_id": nb["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def atts(client, h, exp):
    return {a["filename"]: a for a in client.get(f"/api/experiments/{exp['id']}", headers=h).json()["attachments"]}


def download(client, h, a) -> bytes:
    return client.get(f"/api/attachments/{a['id']}", headers=h).content


def settle():
    scan(10)
    scan(400)


def test_agilent_d_folder_arrives_as_one_zip_with_its_report(client, users, notebook, watch):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    d = watch("chromatography")
    run = d / "2026-10-21 seq" / f"{exp['eid']}-purity.D"
    (run / "AcqData").mkdir(parents=True)
    raw = os.urandom(200_000)
    (run / "AcqData" / "MSScan.bin").write_bytes(raw)
    (run / "Report.pdf").write_bytes(b"%PDF report")
    (d / "2026-10-21 seq" / f"{exp['eid']}-purity export.csv").write_text("time,mAU\n0,0\n")  # saved beside it
    settle()
    a = atts(client, h, exp)
    name = f"{exp['eid']}-purity.D"
    assert set(a) == {f"{name}.zip", f"{name} Report.pdf", f"{exp['eid']}-purity export.csv"}
    z = zipfile.ZipFile(io.BytesIO(download(client, h, a[f"{name}.zip"])))
    assert z.read(f"{name}/AcqData/MSScan.bin") == raw
    assert z.getinfo(f"{name}/AcqData/MSScan.bin").compress_type == zipfile.ZIP_STORED  # raw data isn't recompressed
    assert download(client, h, a[f"{name} Report.pdf"]) == b"%PDF report"


def test_a_run_still_being_written_is_left_until_quiet(client, users, notebook, watch):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    d = watch("chromatography")
    run = d / f"{exp['eid']}.D"
    run.mkdir()
    (run / "data.ms").write_bytes(b"a" * 100)
    scan(10)
    for t in (100, 200, 300):  # the run goes on: a file inside keeps growing
        with open(run / "data.ms", "ab") as f:
            f.write(b"b" * 100)
        scan(t)
        assert not atts(client, h, exp), f"taken at {t} s while still being written"
    scan(500)
    assert list(atts(client, h, exp)) == [f"{exp['eid']}.D.zip"]


def test_waters_raw_folder_and_thermo_raw_file(client, users, notebook, watch):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    d = watch("chromatography")
    (d / f"{exp['eid']}_waters.raw").mkdir()
    (d / f"{exp['eid']}_waters.raw" / "_FUNC001.DAT").write_bytes(b"waters")
    (d / f"{exp['eid']}_thermo.raw").write_bytes(b"thermo raw file")
    settle()
    a = atts(client, h, exp)
    assert set(a) == {f"{exp['eid']}_waters.raw.zip", f"{exp['eid']}_thermo.raw"}
    assert download(client, h, a[f"{exp['eid']}_thermo.raw"]) == b"thermo raw file"


def test_sciex_wiff_travels_with_its_scan_file(client, users, admin, notebook, watch):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    d = watch("chromatography")
    (d / f"{exp['eid']}.wiff").write_bytes(b"wiff")
    (d / f"{exp['eid']}.wiff.scan").write_bytes(b"scan data")
    settle()
    a = atts(client, h, exp)
    assert list(a) == [f"{exp['eid']}.wiff.zip"]
    z = zipfile.ZipFile(io.BytesIO(download(client, h, a[f"{exp['eid']}.wiff.zip"])))
    assert sorted(z.namelist()) == [f"{exp['eid']}.wiff", f"{exp['eid']}.wiff.scan"]
    assert not any(i["path"].endswith(".scan") for i in client.get("/api/inbox", headers=admin).json())


def test_agilent_nmr_fid_folder_matched_by_its_sample_name(client, users, notebook, watch):
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    d = watch("agilent_nmr")
    fid = d / "vnmrsys" / "data" / "PROTON_01.fid"
    fid.mkdir(parents=True)
    (fid / "fid").write_bytes(os.urandom(512))
    (fid / "procpar").write_text(
        'samplename 2 2 0 0 0 2 1 0 1 64\n1 "crude ' + exp["eid"] + '"\n0 \n'
        'temp 1 1 200 -150 0.1 2 1 0 1 64\n1 25\n0 \n')
    settle()
    a = atts(client, h, exp)
    assert list(a) == ["PROTON_01.fid.zip"]
    z = zipfile.ZipFile(io.BytesIO(download(client, h, a["PROTON_01.fid.zip"])))
    assert sorted(z.namelist()) == ["PROTON_01.fid/fid", "PROTON_01.fid/procpar"]


def test_big_files_are_copied_without_filling_memory(tmp_path):
    big = tmp_path / "run.raw"
    with open(big, "wb") as f:
        for _ in range(40):
            f.write(os.urandom(1 << 20))  # 40 MB
    folder = tmp_path / "big.D"
    folder.mkdir()
    os.link(big, folder / "data.ms")
    tracemalloc.start()
    try:
        instruments.pack("run.raw", big, "chromatography")
        instruments.pack("big.D", folder, "chromatography")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * (1 << 20), f"peak {peak / 1e6:.1f} MB"


def test_big_files_stay_out_of_the_obsidian_vault(client, users, notebook, tmp_path, monkeypatch):
    from sqlmodel import Session

    from app.db import engine
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    ent = next(e for e in client.get(f"/api/experiments/{exp['id']}", headers=h).json()["entries"] if e["kind"] == "characterization")
    small = client.post(f"/api/experiments/{exp['id']}/attachments", headers=h, files={"file": ("small.jdx", io.BytesIO(b"##TITLE=s\n##END=\n"))}).json()
    big = client.post(f"/api/experiments/{exp['id']}/attachments", headers=h, files={"file": ("big.raw", io.BytesIO(b"x" * 5000))}).json()
    r = client.post(f"/api/experiments/{exp['id']}/entries", headers=h, json={"kind": "file", "title": "Data",
                                                                            "content": {"attachment_ids": [small["id"], big["id"]]}})
    assert r.status_code in (200, 201), r.text
    monkeypatch.setattr(export_md, "VAULT_MAX_BYTES", 1000)
    vault = tmp_path / "vault"
    with Session(engine) as s:
        export_md.sync_vault(s, vault)
    files = {p.name for p in vault.rglob("*") if p.is_file()}
    assert any(n.endswith("small.jdx") for n in files) and not any(n.endswith("big.raw") for n in files)
    note = next(p for p in vault.rglob(f"{exp['eid']} *.md")).read_text()
    assert "big.raw" in note and "kept in Benchtop" in note
    assert ent  # (the default characterisation section is untouched)


def test_the_integrity_check_reads_big_files_in_pieces(client, users, notebook):
    """Regression: ./benchtop check read each attached file whole into memory (gigabytes, for LC-MS data)."""
    from app import check
    h = users["alice"]
    exp = new_exp(client, h, notebook)
    data = os.urandom(30 << 20)
    r = client.post(f"/api/experiments/{exp['id']}/attachments", headers=h, files={"file": ("big.ms", io.BytesIO(data))})
    assert r.status_code == 201, r.text
    del data
    tracemalloc.start()
    try:
        res = check.check_live()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert res["ok"], res
    assert peak < 20 * (1 << 20), f"peak {peak / 1e6:.1f} MB"
