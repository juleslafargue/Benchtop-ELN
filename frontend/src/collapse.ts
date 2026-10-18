// Remembered open/closed state for collapsible parts of the page (per browser: it's a viewing preference).
import { useCallback, useEffect, useState } from 'react'

const KEY = 'benchtop.collapsed'
const read = (): Record<string, true> => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} } }
const write = (m: Record<string, true>) => { try { localStorage.setItem(KEY, JSON.stringify(m)) } catch { /* private mode */ } }

export function setCollapsedMany(keys: string[], collapsed: boolean) {
  const m = read()
  for (const k of keys) { if (collapsed) m[k] = true; else delete m[k] }
  write(m)
  window.dispatchEvent(new CustomEvent('benchtop:collapse', { detail: { keys, collapsed } }))
}

export function useCollapsed(key: string): [boolean, (v: boolean) => void] {
  const [c, setC] = useState(() => !!read()[key])
  useEffect(() => {
    const on = (e: Event) => { const d = (e as CustomEvent).detail; if (d.keys.includes(key)) setC(d.collapsed) }
    window.addEventListener('benchtop:collapse', on)
    return () => window.removeEventListener('benchtop:collapse', on)
  }, [key])
  const set = useCallback((v: boolean) => setCollapsedMany([key], v), [key])
  return [c, set]
}

export function isCollapsed(key: string) { return !!read()[key] }
