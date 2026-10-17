import { useState } from 'react'
import { useApp } from '../App'
import { api, type Preferences, type User } from '../api'
import { initials } from '../components/Layout'
import PasswordDialog from '../components/PasswordDialog'
import Modal from '../components/Modal'

function UsernameDialog({ current, onDone, onClose }: { current: string; onDone: (u: User) => void; onClose: () => void }) {
  const [name, setName] = useState(current)
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const save = async () => {
    try { onDone(await api.post<User>('/api/auth/username', { new_username: name, password })) } catch (e: any) { setErr(e.message) }
  }
  return (
    <Modal title="Change username" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!name.trim() || !password || name === current} onClick={save}>Change username</button></>}>
      <div className="stack">
        <label className="field">New username<input className="input" autoFocus value={name} onChange={e => setName(e.target.value.toLowerCase())} /></label>
        <p className="faint" style={{ margin: 0 }}>2–32 characters: lower-case letters, digits, “.”, “_” or “-”. You'll sign in with this from now on. Past records keep showing who did what.</p>
        <label className="field">Current password<input className="input" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label>
        {err && <p className="error">{err}</p>}
      </div>
    </Modal>
  )
}

export default function Account() {
  const { user, setUser, notebooks, toast } = useApp()
  const [name, setName] = useState(user.full_name)
  const [email, setEmail] = useState(user.email)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [pw, setPw] = useState(false)
  const [uname, setUname] = useState(false)
  const dirty = name.trim() !== user.full_name || email.trim() !== user.email

  const saveProfile = async () => {
    setBusy(true); setErr('')
    try { setUser(await api.patch<User>('/api/auth/me', { full_name: name, email })); toast('Profile saved') }
    catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  const setPref = async (p: Preferences) => {
    try { setUser(await api.patch<User>('/api/auth/me', { preferences: p })) } catch (e: any) { toast(e.message) }
  }
  const theme = user.preferences?.theme || 'system'

  return (
    <div className="stack" style={{ gap: 22 }}>
      <div className="row" style={{ gap: 14 }}>
        <span className="avatar lg" aria-hidden>{initials(name || user.username)}</span>
        <div><h1 className="page-title">Account</h1><p className="lede" style={{ margin: 0 }}>Signed in as <b>{user.username}</b> · {user.role}</p></div>
      </div>

      <div className="settings">
        <div><h2>Profile</h2><p className="help">How you appear to colleagues and on records.</p></div>
        <form className="panel" onSubmit={e => { e.preventDefault(); if (dirty) saveProfile() }}>
          <div className="section stack" style={{ gap: 14 }}>
            <label className="field">Full name<input className="input" value={name} onChange={e => setName(e.target.value)} autoComplete="name" /></label>
            <div>
              <div className="faint" style={{ marginBottom: 6 }}>Your signatures will read</div>
              <div className="sig-preview">{name.trim() || user.username}</div>
              <p className="help" style={{ marginTop: 6 }}>Records you've already signed keep the name you used at the time.</p>
            </div>
            <label className="field">Email<input className="input" type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" placeholder="you@university.edu" /></label>
            {err && <p className="error" style={{ margin: 0 }}>{err}</p>}
            <div className="row"><button className="btn primary" disabled={!dirty || busy}>Save profile</button>
              {dirty && <button type="button" className="btn ghost" onClick={() => { setName(user.full_name); setEmail(user.email) }}>Discard changes</button>}</div>
          </div>
        </form>

        <div><h2>Sign-in</h2><p className="help">Your username and password. Both changes are recorded in the audit trail.</p></div>
        <div className="panel">
          <div className="section spread" style={{ alignItems: 'center' }}>
            <div><div className="faint">Username</div><b style={{ fontWeight: 600 }}>{user.username}</b></div>
            <button className="btn" onClick={() => setUname(true)}>Change username</button>
          </div>
          <div className="section spread" style={{ alignItems: 'center' }}>
            <div><div className="faint">Password</div>••••••••</div>
            <button className="btn" onClick={() => setPw(true)}>Change password</button>
          </div>
        </div>

        <div><h2>Preferences</h2><p className="help">Saved to your account, so they follow you to other computers.</p></div>
        <div className="panel">
          <div className="section">
            <div className="faint" style={{ marginBottom: 6 }}>Appearance</div>
            <div className="choice" role="group" aria-label="Appearance">
              {(['system', 'light', 'dark'] as const).map(t => (
                <button key={t} aria-pressed={theme === t} onClick={() => setPref({ theme: t })}>{{ system: 'Match system', light: 'Light', dark: 'Dark' }[t]}</button>
              ))}
            </div>
          </div>
          <div className="section">
            <label className="field" style={{ maxWidth: 360 }}>Default notebook for new experiments
              <select className="select" value={user.preferences?.default_notebook_id ?? ''} onChange={e => setPref({ default_notebook_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">First in the list</option>
                {notebooks.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
              </select>
            </label>
          </div>
          <div className="section">
            <label className="row" style={{ gap: 8 }}>
              <input type="checkbox" checked={!!user.preferences?.spectrum_expanded} onChange={e => setPref({ spectrum_expanded: e.target.checked })} />
              Open spectra full-screen
            </label>
          </div>
        </div>
      </div>
      {pw && <PasswordDialog onClose={() => setPw(false)} />}
      {uname && <UsernameDialog current={user.username} onClose={() => setUname(false)} onDone={u => { setUser(u); setUname(false); toast('Username changed') }} />}
    </div>
  )
}
