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
}
