"""v0.13.1: colour themes."""
import importlib.util
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _themes_module():
    spec = importlib.util.spec_from_file_location("themes", FRONTEND / "scripts" / "themes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_palette_preference_is_saved_and_validated(client, users):
    h = users["alice"]
    r = client.patch("/api/auth/me", headers=h, json={"preferences": {"palette": "notebook"}})
    assert r.json()["preferences"]["palette"] == "notebook"
    r = client.patch("/api/auth/me", headers=h, json={"preferences": {"palette": "<script>"}})
    assert r.json()["preferences"]["palette"] == "notebook"  # unknown names are ignored
    client.patch("/api/auth/me", headers=h, json={"preferences": {"palette": "benchtop"}})


def test_server_knows_every_theme():
    from app.routers.auth import PALETTES
    assert set(PALETTES) == set(_themes_module().THEMES)


def test_generated_theme_files_are_up_to_date():
    t = _themes_module()
    assert (FRONTEND / "src" / "themes.css").read_text() == t.css(), "run: python3 frontend/scripts/themes.py"
    assert (FRONTEND / "src" / "themeList.ts").read_text() == t.ts(), "run: python3 frontend/scripts/themes.py"


def test_every_theme_is_readable():
    assert _themes_module().check(), "a theme has text below the contrast minimums"
