// The colour theme ("palette") on <html data-palette>. Saved to the account; this browser keeps a copy so
// the sign-in page and the first moments after loading already use it.
import { THEMES } from './themeList'

const KEY = 'benchtop.palette'
const known = (p: unknown): p is string => typeof p === 'string' && THEMES.some(t => t.id === p)

export function applyPalette(p?: string | null) {
  let id = p
  if (id === undefined) { try { id = localStorage.getItem(KEY) } catch { id = null } }
  const root = document.documentElement
  if (known(id) && id !== 'benchtop') root.dataset.palette = id
  else delete root.dataset.palette
  if (p !== undefined) { try { if (known(p) && p !== 'benchtop') localStorage.setItem(KEY, p); else localStorage.removeItem(KEY) } catch { /* private mode */ } }
  syncThemeColor()
}

/** Chrome / Safari tint the installed app's title bar with <meta name="theme-color">: follow the theme in use. */
export function syncThemeColor() {
  const paper = getComputedStyle(document.documentElement).getPropertyValue('--paper').trim()
  if (!paper) return
  const metas = document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')
  metas.forEach((m, i) => { if (i === 0) { m.removeAttribute('media'); m.content = paper } else m.remove() })
}

/** Installed app with Chrome's compact title bar ("window controls overlay"): mark <html data-wco> so the page
 *  leaves room for the title bar strip and Chrome's buttons sit centred in it. */
export function watchTitleBar() {
  const wco = (navigator as any).windowControlsOverlay
  if (!wco) return
  const sync = () => document.documentElement.toggleAttribute('data-wco', !!wco.visible)
  wco.addEventListener?.('geometrychange', sync)
  sync()
}
