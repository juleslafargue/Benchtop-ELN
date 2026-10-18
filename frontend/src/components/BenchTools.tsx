import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../App'
import { api } from '../api'
import { cancelTimer, fmtDuration, logLine, parseTimer, startTimer, useTimers } from '../bench'
import Modal from './Modal'

/** Running timers, in the sidebar (and on the phone view). */
export function TimerTray({ compact }: { compact?: boolean }) {
  const timers = useTimers()
  const { toast } = useApp()
  useEffect(() => {
    const done = (e: Event) => { const t = (e as CustomEvent).detail; toast(`⏱ ${t.label} (${t.eid}) is done`) }
    window.addEventListener('benchtop:timer-done', done)
    return () => window.removeEventListener('benchtop:timer-done', done)
  }, [toast])
  if (!timers.length) return null
  const now = Date.now()
  return (
    <div className={`timer-tray${compact ? ' compact' : ''}`} aria-label="Timers">
      {!compact && <div className="side-heading"><span>Timers</span></div>}
      {timers.map(t => {
        const left = (t.ends - now) / 1000
        const frac = Math.min(1, Math.max(0, 1 - left / t.seconds))
        return (
          <div key={t.id} className={`timer${t.done ? ' done' : ''}`} style={{ ['--p' as any]: frac }}>
            <Link to={`/experiments/${t.experimentId}`} className="timer-main" title={`${t.eid}: ${t.label}`}>
              <span className="timer-time">{t.done ? 'Done' : fmtDuration(left)}</span>
              <span className="timer-label">{t.label}<span className="faint"> · {t.eid}</span></span>
            </Link>
            <button className="timer-x" onClick={() => cancelTimer(t.id)} aria-label={t.done ? `Dismiss ${t.label}` : `Cancel ${t.label}`}
              title={t.done ? 'Dismiss' : 'Cancel'}>×</button>
          </div>)
      })}
    </div>
  )
}

/** The bench log input: one line, Enter to log. A duration ("30 min stir") starts a timer instead. */
export function BenchBar({ experimentId, eid, onClose, initialTimer }: { experimentId: number; eid: string; onClose: () => void; initialTimer?: boolean }) {
  const { toast } = useApp()
  const [text, setText] = useState(initialTimer ? 'min ' : '')
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { ref.current?.focus(); if (initialTimer) ref.current?.setSelectionRange(0, 0) }, [initialTimer])
  const timer = parseTimer(text)
  const submit = async () => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      if (timer) { startTimer(experimentId, eid, timer.seconds, timer.label); toast(`Timer started: ${timer.label}`) }
      else { await logLine(experimentId, t); toast('Logged') }
      setText('')
    } catch (e: any) { toast(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="bench-bar" role="dialog" aria-label="Bench log">
      <span className="bench-clock" aria-hidden>{new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })}</span>
      <input ref={ref} className="input" value={text} onChange={e => setText(e.target.value)} disabled={busy}
        placeholder="What just happened? e.g. “added NaH portionwise, gas evolution”, or “30 min stir” for a timer"
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit() } if (e.key === 'Escape') onClose() }} aria-label="Bench log note" />
      <button className="btn primary sm" onClick={submit} disabled={!text.trim() || busy}>{timer ? `Start ${fmtDuration(timer.seconds)} timer` : 'Log'}</button>
      <button className="btn ghost sm" onClick={onClose} aria-label="Close bench log">Esc</button>
    </div>
  )
}

/** QR code that opens this experiment on a phone, signed in. */
export function PhoneDialog({ experimentId, onClose }: { experimentId: number; onClose: () => void }) {
  const [link, setLink] = useState<{ url: string; svg: string; reachable: boolean; hint: string; expires_in: number } | null>(null)
  const [err, setErr] = useState('')
  const [left, setLeft] = useState(0)
  const make = () => api.post<NonNullable<typeof link>>(`/api/experiments/${experimentId}/phone-link`).then(l => { setLink(l); setLeft(l.expires_in) }).catch(e => setErr(e.message))
  useEffect(() => { make() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const iv = setInterval(() => setLeft(s => Math.max(0, s - 1)), 1000); return () => clearInterval(iv) }, [])
  return (
    <Modal title="Open on your phone" onClose={onClose} footer={<button className="btn" onClick={onClose}>Done</button>}>
      {err && <p className="error">{err}</p>}
      {link && <div className="phone-qr">
        {!link.reachable && <p className="notice">{link.hint}</p>}
        <div className={`qr${left === 0 ? ' expired' : ''}`} dangerouslySetInnerHTML={{ __html: link.svg }} aria-label="QR code" role="img" />
        <div>
          <p>Point your phone's camera at the code. It opens this experiment's <b>bench view</b>, already signed in as you: take photos of TLC plates or the flask, log what happens, and run timers, one-handed.</p>
          {link.reachable && link.hint && <p className="faint">{link.hint}</p>}
          <p className="faint">{left > 0 ? `The code works once, for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} more.` : 'This code has expired.'}{' '}
            <button className="btn ghost sm" onClick={make}>New code</button></p>
          <p className="faint" style={{ fontSize: 12 }}>The phone gets its own session; you can sign it out from Account settings.</p>
        </div>
      </div>}
    </Modal>
  )
}
