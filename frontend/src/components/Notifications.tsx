import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { ago, api, displayName, type Notification } from '../api'
import { initials } from './Layout'

const ICON: Record<string, string> = { mention: '@', reply: '↩', comment: '💬', share: '⇪', group: '👥', witness_request: '✍', witness_reminder: '⏰', witnessed: '✓', returned: '↩', witness_cancelled: '–' }

/** Bell with unread count; the list opens as a popover. Polls every minute and on navigation. */
export default function Notifications() {
  const [data, setData] = useState<{ items: Notification[]; unread: number }>({ items: [], unread: 0 })
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  const nav = useNavigate()
  const loc = useLocation()
  const ref = useRef<HTMLDivElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  const load = useCallback(() => { api.get<{ items: Notification[]; unread: number }>('/api/notifications?limit=25').then(setData).catch(() => {}) }, [])
  useEffect(() => { load(); const t = setInterval(load, 60_000); return () => clearInterval(t) }, [load])
  useEffect(() => { const t = setTimeout(load, 1200); return () => clearTimeout(t) }, [loc.pathname, load])
  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node) && !pop.current?.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    addEventListener('mousedown', down); addEventListener('keydown', key)
    return () => { removeEventListener('mousedown', down); removeEventListener('keydown', key) }
  }, [open])

  const go = async (n: Notification) => {
    setOpen(false)
    if (!n.read) api.post('/api/notifications/read', { ids: [n.id] }).then(load).catch(() => {})
    if (n.experiment) nav(`/experiments/${n.experiment.id}${n.comment_id ? `#comment-${n.comment_id}` : ''}`)
    else if (n.kind === 'group') nav('/groups')
  }
  const readAll = async () => { await api.post('/api/notifications/read', { all: true }).catch(() => {}); load() }

  return (
    <div className="notif" ref={ref}>
      <button className={`notif-btn${data.unread ? ' has' : ''}`} aria-label={`Notifications${data.unread ? `, ${data.unread} unread` : ''}`}
        aria-expanded={open} onClick={e => {
          // the sidebar clips overflow, so the popover is fixed to the viewport next to the bell
          const r = (e.currentTarget as HTMLElement).getBoundingClientRect(), w = Math.min(360, innerWidth - 24)
          setPos({ top: r.bottom + 6, left: Math.max(12, Math.min(r.left, innerWidth - w - 12)) }); setOpen(o => !o)
        }}>
        <svg viewBox="0 0 16 16" width="17" height="17" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 11.5V7a4 4 0 0 1 8 0v4.5l1.2 1.3H2.8zM6.6 14.2a1.6 1.6 0 0 0 2.8 0" /></svg>
        {data.unread > 0 && <span className="notif-count">{data.unread > 99 ? '99+' : data.unread}</span>}
      </button>
      {open && createPortal(<div ref={pop} className="notif-pop" role="dialog" aria-label="Notifications" style={{ top: pos.top, left: pos.left }}>
        <div className="notif-head"><b>Notifications</b>{data.unread > 0 && <button className="link-btn" onClick={readAll}>Mark all read</button>}</div>
        {data.items.length === 0 ? <p className="faint notif-empty">Nothing yet. You'll hear here when someone mentions you, replies to you, comments on your experiments or shares one with you.</p>
          : <ul>{data.items.map(n => (
            <li key={n.id} className={n.read ? '' : 'unread'}>
              <button onClick={() => go(n)}>
                <span className="avatar xs" aria-hidden>{n.actor ? initials(n.actor.full_name || n.actor.username) : ICON[n.kind] || '•'}</span>
                <span className="notif-text"><b>{displayName(n.actor)}</b> {n.text}<span className="faint">{ago(n.created_at)}</span></span>
              </button>
            </li>))}</ul>}
      </div>, document.body)}
    </div>
  )
}
