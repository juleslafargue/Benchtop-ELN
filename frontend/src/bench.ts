// Bench helpers shared by the experiment page, the sidebar and the phone view:
// the timestamped bench log and countdown timers.
import { useEffect, useState } from 'react'
import { api, type Entry } from './api'

/** Local clock for log lines: the server may run in another time zone. */
export function stamp(d = new Date()) {
  return {
    time: d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false }),
    day: d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }),
  }
}

export async function logLine(experimentId: number, text: string, attachmentId?: number) {
  const r = await api.post<Entry>(`/api/experiments/${experimentId}/log`, { text, ...stamp(), ...(attachmentId ? { attachment_id: attachmentId } : {}) })
  window.dispatchEvent(new CustomEvent('benchtop:logged', { detail: { experimentId } }))
  return r
}

/** "30 min stir", "stir 30 min", "1.5 h reflux", "90 s" → a timer, or null for an ordinary note. */
export function parseTimer(text: string): { seconds: number; label: string } | null {
  const re = /(\d+(?:[.,]\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?)\b/i
  const m = re.exec(text)
  if (!m) return null
  const rest = (text.slice(0, m.index) + text.slice(m.index + m[0].length)).replace(/^\s*(timer|for)\s*/i, '').trim()
  // only when the note is basically "duration + a word or two", so "stirred for 30 min then quenched" stays a note
  if (rest.split(/\s+/).filter(Boolean).length > 3 || /\b(then|after|was|were|had)\b/i.test(rest)) return null
  const n = parseFloat(m[1].replace(',', '.'))
  const u = m[2].toLowerCase()
  const seconds = Math.round(n * (u.startsWith('h') ? 3600 : u.startsWith('s') ? 1 : 60))
  if (seconds < 5 || seconds > 7 * 86400) return null
  return { seconds, label: rest || 'Timer' }
}

export function fmtDuration(s: number) {
  s = Math.max(0, Math.round(s))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}
const human = (s: number) => s >= 3600 ? `${+(s / 3600).toFixed(2)} h` : s >= 60 ? `${+(s / 60).toFixed(1)} min` : `${s} s`

// ---------------------------------------------------------------- timers
// Kept in this browser (localStorage) so they survive reloads; when one ends it rings, shows a
// notification and writes a line to that experiment's bench log (once, even with several tabs open).

export interface Timer { id: string; label: string; experimentId: number; eid: string; seconds: number; started: number; ends: number; done?: boolean }
const KEY = 'benchtop.timers'
const read = (): Timer[] => { try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] } }
const write = (t: Timer[]) => { try { localStorage.setItem(KEY, JSON.stringify(t)) } catch { /* private mode */ } window.dispatchEvent(new Event('benchtop:timers')) }

export function startTimer(experimentId: number, eid: string, seconds: number, label: string) {
  const now = Date.now()
  const t: Timer = { id: `${now}-${Math.random().toString(36).slice(2, 7)}`, label: label || 'Timer', experimentId, eid, seconds, started: now, ends: now + seconds * 1000 }
  write([...read(), t])
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') Notification.requestPermission().catch(() => {})
  logLine(experimentId, `⏱ Started ${human(seconds)} timer: ${t.label}`).catch(() => {})
  return t
}
export function cancelTimer(id: string) { write(read().filter(t => t.id !== id)) }
export function dismissTimer(id: string) { cancelTimer(id) }

function ring() {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)()
    ;[0, 0.35, 0.7].forEach(at => {
      const o = ctx.createOscillator(), g = ctx.createGain()
      o.frequency.value = 880; o.connect(g); g.connect(ctx.destination)
      g.gain.setValueAtTime(0.0001, ctx.currentTime + at)
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + at + 0.02)
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + at + 0.3)
      o.start(ctx.currentTime + at); o.stop(ctx.currentTime + at + 0.32)
    })
  } catch { /* no audio */ }
}

let ticking = false
/** One loop per tab checks for finished timers. */
function tick() {
  const now = Date.now()
  const all = read()
  let changed = false
  for (const t of all) {
    if (!t.done && t.ends <= now) {
      t.done = true; changed = true
      // claim it so only one tab logs and rings
      const claim = `benchtop.timer-done.${t.id}`
      let mine = true
      try { if (localStorage.getItem(claim)) mine = false; else localStorage.setItem(claim, '1') } catch { /* ok */ }
      if (mine) {
        ring()
        const fmt = (ms: number) => new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
        logLine(t.experimentId, `⏱ ${t.label}: ${human(t.seconds)} done (${fmt(t.started)}–${fmt(t.ends)})`).catch(() => {})
        try { if (typeof Notification !== 'undefined' && Notification.permission === 'granted') new Notification(`${t.label} — done`, { body: `${t.eid}: ${human(t.seconds)} timer finished` }) } catch { /* ok */ }
        window.dispatchEvent(new CustomEvent('benchtop:timer-done', { detail: t }))
      }
    }
  }
  if (changed) write(all)
}

export function useTimers() {
  const [timers, setTimers] = useState<Timer[]>(read)
  const [, setNow] = useState(0)
  useEffect(() => {
    const on = () => setTimers(read())
    window.addEventListener('benchtop:timers', on)
    window.addEventListener('storage', on)
    if (!ticking) { ticking = true; window.setInterval(tick, 1000) }
    const iv = window.setInterval(() => setNow(n => n + 1), 1000)
    return () => { window.removeEventListener('benchtop:timers', on); window.removeEventListener('storage', on); clearInterval(iv) }
  }, [])
  return timers
}
