"""v0.13.6: Benchtop can be installed as an app from the browser (its own window and Dock icon)."""
import json
import struct
from pathlib import Path

PUBLIC = Path(__file__).resolve().parents[2] / "frontend" / "public"
SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_manifest_and_icons():
    m = json.loads((PUBLIC / "manifest.webmanifest").read_text())
    assert m["name"] == "Benchtop" and m["start_url"] == "/" and m["display"] == "standalone"
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes and any(i.get("purpose") == "maskable" for i in m["icons"])
    for icon in m["icons"]:
        data = (PUBLIC / icon["src"].lstrip("/")).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = struct.unpack(">II", data[16:24])
        assert f"{w}x{h}" == icon["sizes"], icon["src"]
    assert 'rel="manifest"' in (PUBLIC.parent / "index.html").read_text()


def test_nothing_is_cached_offline():
    """Upgrade safety: no service worker that could keep serving an old copy of Benchtop."""
    assert not (PUBLIC / "sw.js").exists()
    assert "serviceWorker.register" not in "".join(p.read_text() for p in SRC.rglob("*.ts*"))
