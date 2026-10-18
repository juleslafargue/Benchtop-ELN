import { useEffect, useState } from 'react'
import { useApp } from '../App'
import type { NmrStatus } from '../components/Prediction'
import { api, when, type AuditEvent, type Role, type User } from '../api'
import Modal from '../components/Modal'
import { Plugins, Webhooks } from '../components/Integrations'
import PasswordField from '../components/PasswordField'

function EditUser({ u, onClose, onSaved }: { u: User; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ username: u.username, full_name: u.full_name, email: u.email })
  const [err, setErr] = useState('')
  const save = async () => {
    const changes = Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== (u as any)[k]))
    if (!Object.keys(changes).length) { onClose(); return }
    try { await api.patch(`/api/users/${u.id}`, changes); onSaved() } catch (e: any) { setErr(e.message) }
  }
  return (
    <Modal title={`Edit ${u.username}`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" onClick={save}>Save changes</button></>}>
      <div className="form-grid">
        <label className="field">Username<input className="input" value={f.username} onChange={e => setF({ ...f, username: e.target.value.toLowerCase() })} /></label>
        <label className="field">Full name<input className="input" value={f.full_name} onChange={e => setF({ ...f, full_name: e.target.value })} /></label>
        <label className="field">Email<input className="input" type="email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} /></label>
      </div>
      <p className="faint">Signed records keep the name the person used when signing.</p>
      {err && <p className="error">{err}</p>}
    </Modal>
  )
}

function UserForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ username: '', full_name: '', email: '', password: '', role: 'scientist' as Role, must_change: true })
  const [err, setErr] = useState('')
  const [valid, setValid] = useState(false)
  const save = async () => { try { await api.post('/api/users', f); onSaved() } catch (e: any) { setErr(e.message) } }
  return (
    <Modal title="Add user" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!f.username || !valid} onClick={save}>Add user</button></>}>
      <div className="form-grid">
        <label className="field">Username<input className="input" autoFocus value={f.username} onChange={e => setF({ ...f, username: e.target.value })} /></label>
        <label className="field">Full name<input className="input" value={f.full_name} onChange={e => setF({ ...f, full_name: e.target.value })} /></label>
        <label className="field">Email<input className="input" type="email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} /></label>
        <label className="field">Role<select className="select" value={f.role} onChange={e => setF({ ...f, role: e.target.value as Role })}>
          <option value="scientist">Scientist</option><option value="viewer">Viewer (read-only)</option><option value="admin">Admin</option></select></label>
      </div>
      <PasswordField label="First password (give it to them privately)" value={f.password} onChange={v => setF({ ...f, password: v })} onValid={setValid}
        username={f.username} fullName={f.full_name} email={f.email} />
      <label className="check-row" style={{ marginTop: 8 }}><input type="checkbox" checked={f.must_change} onChange={e => setF({ ...f, must_change: e.target.checked })} />
        Ask them to choose their own password when they first sign in (recommended: you know this one)</label>
      {err && <p className="error">{err}</p>}
    </Modal>
  )
}

function ResetPassword({ u, onClose, onDone }: { u: User; onClose: () => void; onDone: () => void }) {
  const [pw, setPw] = useState('')
  const [valid, setValid] = useState(false)
  const [err, setErr] = useState('')
  const save = async () => { try { await api.patch(`/api/users/${u.id}`, { password: pw }); onDone() } catch (e: any) { setErr(e.message) } }
  return (
    <Modal title={`Reset ${u.username}'s password`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!valid} onClick={save}>Reset password</button></>}>
      <p style={{ marginTop: 0 }}>Set a temporary password and give it to them privately. They'll be signed out everywhere now and asked to choose their own password the next time they sign in.</p>
      <PasswordField label="Temporary password" value={pw} onChange={setPw} onValid={setValid} username={u.username} fullName={u.full_name} email={u.email} autoFocus />
      {err && <p className="error">{err}</p>}
    </Modal>
  )
}

interface BackupInfo { directory: string; keep: number; interval_hours: number; backups: { name: string; size: number; created_at: string }[] }

function NmrData() {
  const [st, setSt] = useState<NmrStatus | null>(null)
  const [err, setErr] = useState('')
  const load = () => api.get<NmrStatus>('/api/nmr/status').then(setSt).catch(e => setErr(e.message))
  useEffect(() => { load() }, [])
  useEffect(() => { if (!st?.updating) return; const t = setInterval(load, 2500); return () => clearInterval(t) }, [st?.updating])
  const start = async () => { setErr(''); try { setSt(await api.post<NmrStatus>('/api/nmr/update', {})) } catch (e: any) { setErr(e.message) } }
  const m = st?.meta
  return (
    <section className="stack" style={{ gap: 8 }}>
      <div className="spread"><h2 style={{ fontSize: 17 }}>NMR prediction data</h2>
        <button className="btn sm" disabled={!st || st.updating} onClick={start}>{st?.updating ? 'Updating…' : st?.available ? 'Update now' : 'Download'}</button></div>
      <p className="faint" style={{ margin: 0 }}>Predicted ¹H and ¹³C shifts come from NMRShiftDB2, an open database of assigned spectra (nmrshiftdb2 data licence: attribution, share-alike).
        Benchtop ships with a table built from a 2023 extract; it checks every {st?.refresh_days ?? 30} days while running and rebuilds when NMRShiftDB2 publishes a newer export (or now, with Update now or <code>./benchtop update-nmr-data</code>). Downloading and building takes a few minutes.</p>
      {st?.updating && <p style={{ margin: 0 }}><b>Working:</b> {st.progress}</p>}
      {st?.last_error && <p className="error" style={{ margin: 0 }}>Last update failed: {st.last_error}</p>}
      {err && <p className="error" style={{ margin: 0 }}>{err}</p>}
      {m ? <dl className="kv"><dt>Source</dt><dd>{m.source}</dd><dt>Table</dt><dd>{m.table}</dd>
        <dt>Built</dt><dd>{when(m.built_at)}{st?.age_days != null ? ` · ${Math.round(st.age_days)} days ago` : ''}</dd>
        <dt>Structures</dt><dd>{Number(m.molecules).toLocaleString()}</dd>
        <dt>Assigned shifts</dt><dd>{Number(m.assignments_13C).toLocaleString()} ¹³C · {Number(m.assignments_1H).toLocaleString()} ¹H</dd>
        <dt>Checks for updates</dt><dd>{(st as any)?.auto_update ? `monthly${(st as any)?.last_check ? `, last ${when((st as any).last_check)}` : ''}` : 'off (BENCHTOP_NMR_AUTO_UPDATE=off)'}</dd></dl>
        : st && !st.updating && <p className="faint" style={{ margin: 0 }}>No prediction table found.</p>}
    </section>
  )
}

function Backups() {
  const { toast } = useApp()
  const [info, setInfo] = useState<BackupInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const load = () => api.get<BackupInfo>('/api/admin/backups').then(setInfo)
  useEffect(() => { load() }, [])
  const now = async () => {
    setBusy(true); setErr('')
    try { await api.post('/api/admin/backups'); toast('Backup written'); load() } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <section className="stack" style={{ gap: 8 }}>
      <div className="spread"><h2 style={{ fontSize: 17 }}>Backups</h2>
        <button className="btn sm" disabled={busy} onClick={now}>{busy ? 'Backing up…' : 'Back up now'}</button></div>
      {info && <p className="faint" style={{ margin: 0 }}>
        {info.interval_hours > 0 ? `Automatic every ${info.interval_hours} h` : 'Automatic backups are off'}, keeping the newest {info.keep}.
        Saved in <code>{info.directory}</code>. To restore, stop Benchtop and run <code>./benchtop restore &lt;file&gt;</code>.</p>}
      {err && <p className="error">{err}</p>}
      <div className="panel grid-wrap" style={{ border: 0 }}>
        <table className="plain"><thead><tr><th>File</th><th>Made</th><th>Size</th></tr></thead>
          <tbody>{(info?.backups || []).map(b => (
            <tr key={b.name}><td>{b.name}</td><td>{when(b.created_at)}</td><td className="num">{(b.size / 1e6).toFixed(1)} MB</td></tr>))}
            {info && !info.backups.length && <tr><td colSpan={3} className="faint">No backups yet.</td></tr>}</tbody></table>
      </div>
    </section>
  )
}

export default function Admin() {
  const { user: me, toast } = useApp()
  const [users, setUsers] = useState<User[]>([])
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [chain, setChain] = useState<any>(null)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<User | null>(null)
  const load = () => { api.get<User[]>('/api/users').then(setUsers); api.get<AuditEvent[]>('/api/audit?limit=150').then(setEvents) }
  useEffect(load, [])

  const toggle = async (u: User) => { await api.patch(`/api/users/${u.id}`, { active: !u.active }); load() }
  const role = async (u: User, r: Role) => { await api.patch(`/api/users/${u.id}`, { role: r }); load() }
  const [resetting, setResetting] = useState<User | null>(null)
  const signOut = async (u: User) => {
    try { const r = await api.post<{ ended: number }>(`/api/users/${u.id}/sign-out`); toast(`Signed ${u.username} out of ${r.ended} device${r.ended === 1 ? '' : 's'}`) }
    catch (e: any) { toast(e.message) }
  }

  return (
    <div className="stack" style={{ gap: 26 }}>
      <div><h1 className="page-title">Users &amp; audit</h1><p className="lede">Manage accounts, plugins, webhooks and backups, and check that the audit trail has not been altered.</p></div>
      <section className="stack" style={{ gap: 8 }}>
        <div className="spread"><h2 style={{ fontSize: 17 }}>Users</h2><button className="btn sm" onClick={() => setAdding(true)}>Add user</button></div>
        <div className="panel grid-wrap" style={{ border: 0 }}>
          <table className="plain"><thead><tr><th>Username</th><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th /></tr></thead>
            <tbody>{users.map(u => (
              <tr key={u.id}><td>{u.username}</td><td>{u.full_name}</td><td className="faint">{u.email}</td>
                <td><select className="select" style={{ padding: '2px 6px' }} value={u.role} disabled={u.id === me.id} onChange={e => role(u, e.target.value as Role)} aria-label={`Role for ${u.username}`}>
                  <option value="scientist">scientist</option><option value="viewer">viewer</option><option value="admin">admin</option></select></td>
                <td>{u.active ? (u.must_change_password ? 'Active · must set password' : u.has_password === false ? 'Active · single sign-on' : 'Active') : 'Disabled'}</td>
                <td style={{ whiteSpace: 'nowrap' }}><button className="btn ghost sm" onClick={() => setEditing(u)}>Edit</button>
                  <button className="btn ghost sm" onClick={() => setResetting(u)}>Reset password</button>
                  {u.id !== me.id && <button className="btn ghost sm" onClick={() => signOut(u)} title="End all of their sessions, e.g. a lost laptop">Sign out everywhere</button>}
                  {u.id !== me.id && <button className="btn ghost sm" onClick={() => toggle(u)}>{u.active ? 'Disable' : 'Enable'}</button>}</td></tr>
            ))}</tbody></table>
        </div>
      </section>
      <Plugins />
      <Webhooks />
      <NmrData />
      <Backups />
      <section className="stack" style={{ gap: 8 }}>
        <div className="spread"><h2 style={{ fontSize: 17 }}>Audit trail</h2>
          <button className="btn sm" onClick={async () => setChain(await api.get('/api/audit/verify'))}>Verify hash chain</button></div>
        {chain && <p className={chain.ok ? 'notice' : 'error'} style={chain.ok ? { borderLeftColor: 'var(--witnessed)' } : undefined}>
          {chain.ok ? `All ${chain.checked} events verified. Chain head ${chain.head.slice(0, 16)}…` : `Chain broken at event #${chain.broken_at} (${chain.reason}). Records from that point may have been altered.`}</p>}
        <div className="panel panel-pad">
          <ul className="timeline">{events.map(e => (
            <li key={e.id}><span className="faint">{when(e.ts)}</span><span>{e.username}</span>
              <span><span className="act">{e.action}</span> {e.object_type} {e.object_id ?? ''} <span className="hash">{JSON.stringify(e.details).slice(0, 120)}</span></span></li>
          ))}</ul>
        </div>
      </section>
      {editing && <EditUser u={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); toast('User updated') }} />}
      {resetting && <ResetPassword u={resetting} onClose={() => setResetting(null)} onDone={() => { setResetting(null); load(); toast(`Password reset; ${resetting.username} was signed out and will choose a new one`) }} />}
      {adding && <UserForm onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load() }} />}
    </div>
  )
}
