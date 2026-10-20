import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="benchtop-test-")
os.environ["BENCHTOP_DATA_DIR"] = _tmp
os.environ["BENCHTOP_ADMIN_PASSWORD"] = "adminpass"
os.environ["BENCHTOP_BACKUP_INTERVAL_HOURS"] = "0"  # tests trigger backups explicitly
os.environ["BENCHTOP_PUBCHEM"] = "off"  # tests inject a mock PubChem
os.environ["BENCHTOP_TIMESTAMPS"] = "off"  # no calls to real timestamp services; tests switch on a fake one
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, u, p):
    r = client.post("/api/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return _login(client, "admin", "adminpass")


@pytest.fixture(scope="session")
def users(client, admin):
    for u, role in (("alice", "scientist"), ("bob", "scientist"), ("vic", "viewer")):
        client.post("/api/users", headers=admin, json={"username": u, "password": "lab-bench-pw-2026", "must_change": False,
                                                       "full_name": u.title(), "role": role})
    return {u: _login(client, u, "lab-bench-pw-2026") for u in ("alice", "bob", "vic")}


@pytest.fixture()
def notebook(client, users):
    return client.post("/api/notebooks", headers=users["alice"], json={"name": "NB"}).json()
