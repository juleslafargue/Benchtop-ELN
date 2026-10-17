import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../App'
import { api, displayName, type Audience, type User, type UserBrief } from '../api'
import Modal from './Modal'
import { AudienceBadge } from './Audience'

interface Shares {
  audience: Audience & { notebook: { id: number; name: string } | null; owner: UserBrief }
  shares: { user: UserBrief; permission: 'view' | 'edit'; created_at: string }[]
  can_share: boolean
}

export default function ShareDialog({ experimentId, eid, onClose, preselect }: {
  experimentId: number; eid: string; onClose: () => void; preselect?: UserBrief[]
}) {
  const { toast, user } = useApp()
  const [data, setData] = useState<Shares | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [who, setWho] = useState(preselect?.[0] ? String(preselect[0].id) : '')
  const [perm, setPerm] = useState<'view' | 'edit'>('view')
  const [err, setErr] = useState('')
  useEffect(() => {
    api.get<Shares>(`/api/experiments/${experimentId}/shares`).then(setData).catch(e => setErr(e.message))
    api.get<User[]>('/api/users').then(setUsers).catch(() => {})
  }, [experimentId])

  const run = async (p: Promise<Shares>, msg: string) => {
    try { setData(await p); setErr(''); toast(msg) } catch (e: any) { setErr(e.message) }
  }
  if (!data) return <Modal title={`Share ${eid}`} onClose={onClose}><p className="faint">{err || 'Loading…'}</p></Modal>
  const taken = new Set([data.audience.owner.id, ...data.shares.map(s => s.user.id)])
  const candidates = users.filter(u => u.active && !taken.has(u.id))
  const target = users.find(u => String(u.id) === who)
  const a = data.audience
  return (
    <Modal title={`Share ${eid}`} onClose={onClose} footer={<button className="btn" onClick={onClose}>Done</button>}>
      <div className="stack" style={{ gap: 14 }}>
        <div className="share-aud">
          <span className="fld-cap">Who can open it</span>
          <div className="row" style={{ gap: 8 }}>
            <AudienceBadge audience={a} />
            <span className="faint">{a.notebook ? <>set by the notebook <Link to={`/notebooks/${a.notebook.id}`} onClick={onClose}>{a.notebook.name}</Link></> : null}
              {' · '}owner {displayName(a.owner)}</span>
          </div>
        </div>
        <div>
          <span className="fld-cap">Also shared with</span>
          {data.shares.length === 0 && <p className="faint" style={{ margin: '4px 0 0' }}>Nobody individually.</p>}
          <ul className="share-list">
            {data.shares.map(s => (
              <li key={s.user.id}>
                <span className="member-name">{displayName(s.user)} <span className="faint">@{s.user.username}</span></span>
                {data.can_share
                  ? <select className="select sm" value={s.permission} aria-label={`Access for ${displayName(s.user)}`}
                      onChange={e => run(api.put(`/api/experiments/${experimentId}/shares/${s.user.id}`, { permission: e.target.value }), 'Updated')}>
                      <option value="view">Can view</option><option value="edit">Can edit</option></select>
                  : <span className="tag">{s.permission === 'edit' ? 'Can edit' : 'Can view'}</span>}
                {(data.can_share || s.user.id === user.id) && <button className="btn ghost sm"
                  onClick={() => run(api.del(`/api/experiments/${experimentId}/shares/${s.user.id}`), `Stopped sharing with ${displayName(s.user)}`)}>Remove</button>}
              </li>
            ))}
          </ul>
        </div>
        {data.can_share && <div>
          <span className="fld-cap">Share with someone else</span>
          <div className="row share-add">
            <select className="select" value={who} onChange={e => setWho(e.target.value)} aria-label="Person">
              <option value="">Choose a person…</option>
              {candidates.map(u => <option key={u.id} value={u.id}>{u.full_name || u.username} (@{u.username}){u.role === 'viewer' ? ' · read-only' : ''}</option>)}
            </select>
            <select className="select" value={target?.role === 'viewer' ? 'view' : perm} disabled={target?.role === 'viewer'}
              onChange={e => setPerm(e.target.value as 'view' | 'edit')} aria-label="Access">
              <option value="view">Can view</option><option value="edit">Can edit</option>
            </select>
            <button className="btn primary" disabled={!who} onClick={() => {
              run(api.put(`/api/experiments/${experimentId}/shares/${who}`, { permission: target?.role === 'viewer' ? 'view' : perm }), `Shared with ${target ? displayName(target) : 'them'}`)
              setWho('')
            }}>Share</button>
          </div>
          <p className="faint" style={{ marginBottom: 0 }}>They get a notification. "Can edit" lets them change sections; only you can sign it.</p>
        </div>}
        {err && <p className="error">{err}</p>}
      </div>
    </Modal>
  )
}
