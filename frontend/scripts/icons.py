"""Benchtop's app icons, drawn from the logo. Run from frontend/ (needs Playwright with Chromium):

    python3 scripts/icons.py

Writes public/icon-192.png and icon-512.png (Mac-style rounded tile: Dock, Launchpad, Chrome/Edge apps),
icon-maskable-512.png (full bleed, logo inside the safe zone: Android and other launchers that crop) and
apple-touch-icon.png (full bleed: iPhone/iPad home screen and Safari's Add to Dock; the system rounds it).
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

LOGO_FILL = 0.76     # the logo's height as a share of the tile (most Mac app icons sit around here)
MASKABLE_FILL = 0.60  # smaller, so circular crops never clip it
PAPER = "#f6f3ef"
LOGO = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-34 -64 120 132" width="{w}" height="{h}">
  <line x1="0" y1="-52" x2="0" y2="36" stroke="#1f2a36" stroke-width="10" stroke-linecap="round"/>
  <polygon points="0,12 26,-3 52,12 52,42 26,57 0,42" fill="none" stroke="#1f2a36" stroke-width="10" stroke-linejoin="round"/>
  <rect x="10" y="21.5" width="32" height="11" rx="5.5" fill="#b8336a" transform="rotate(-30 26 27)"/></svg>'''


def logo(h: float) -> str:
    return LOGO.format(w=h * 120 / 132, h=h)


def tile(size: int) -> str:  # rounded square with a margin and a soft shadow, as Mac app icons are drawn
    pad = size * 0.09
    box = size - 2 * pad
    return (f'<div style="position:absolute;left:{pad}px;top:{pad}px;width:{box}px;height:{box}px;border-radius:{box * 0.225}px;'
            f'background:{PAPER};box-shadow:0 {size * 0.012}px {size * 0.03}px rgba(0,0,0,.18);display:grid;place-items:center">'
            f'{logo(box * LOGO_FILL)}</div>')


def bleed(size: int, fill: float) -> str:
    return f'<div style="width:{size}px;height:{size}px;background:{PAPER};display:grid;place-items:center">{logo(size * fill)}</div>'


ICONS = [("icon-192.png", 192, lambda s: tile(s)), ("icon-512.png", 512, lambda s: tile(s)),
         ("icon-maskable-512.png", 512, lambda s: bleed(s, MASKABLE_FILL)), ("apple-touch-icon.png", 180, lambda s: bleed(s, LOGO_FILL))]

if __name__ == "__main__":
    public = Path(__file__).resolve().parents[1] / "public"
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, size, draw in ICONS:
            pg = b.new_page(viewport={"width": size, "height": size})
            pg.set_content(f'<html><body style="margin:0;background:transparent">{draw(size)}</body></html>')
            pg.screenshot(path=str(public / name), omit_background=True)
            pg.close()
        b.close()
    print("wrote", ", ".join(n for n, _, _ in ICONS))
