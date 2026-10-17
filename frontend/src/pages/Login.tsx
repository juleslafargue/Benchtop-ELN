import { useEffect, useState, type FormEvent } from 'react'
import { api, type User } from '../api'
import PasswordField from '../components/PasswordField'

function Setup({ onLogin }: { onLogin: (token: string, user: User) => void }) {
  const [f, setF] = useState({ full_name: '', username: '', email: '', password: '', confirm: '', load_demo: true })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [valid, setValid] = useState(false)
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const suggested = f.full_name.trim().split(/\s+/)[0]?.toLowerCase().replace(/[^a-z0-9._-]/g, '') || ''

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (f.password !== f.confirm) { setErr('The two passwords are different'); return }
    setBusy(true); setErr('')
    try {
      const r = await api.post<{ token: string; user: User }>('/api/setup', { ...f, username: f.username || suggested })
      onLogin(r.token, r.user)
    } catch (e: any) { setErr(e.message); setBusy(false) }
  }

  return (
    <div className="login">
      <form className="panel panel-pad login-card stack" style={{ width: 'min(460px, 100%)' }} onSubmit={submit}>
        <img src="/flask.svg" alt="" width={36} height={36} />
        <div>
          <h1>Welcome to Benchtop</h1>
          <p className="muted" style={{ margin: 0 }}>Create your account. You'll be the administrator of this notebook.</p>
        </div>
        <label className="field">Your name, as it should appear on signatures
          <input className="input" autoFocus autoComplete="name" value={f.full_name} onChange={set('full_name')} placeholder="e.g. Jules Lafargue" /></label>
        <div className="form-grid" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
          <label className="field">Username<input className="input" autoComplete="username" value={f.username} onChange={e => setF({ ...f, username: e.target.value.toLowerCase() })} placeholder={suggested || 'e.g. jules'} /></label>
          <label className="field">Email (optional)<input className="input" type="email" autoComplete="email" value={f.email} onChange={set('email')} /></label>
        </div>
        <PasswordField label="Password" value={f.password} onChange={v => setF({ ...f, password: v })} onValid={setValid}
          username={f.username || suggested} fullName={f.full_name} email={f.email} />
        <label className="field">Repeat password<input className="input" type="password" autoComplete="new-password" value={f.confirm} onChange={set('confirm')} /></label>
        <label className="check-row">
          <input type="checkbox" checked={f.load_demo} onChange={set('load_demo')} />
          Add a demo notebook to explore (two worked Suzuki couplings, sample reagents, an NMR spectrum)
        </label>
        {err && <p className="error" role="alert" style={{ margin: 0 }}>{err}</p>}
        <button className="btn primary" disabled={busy || !f.full_name.trim() || !valid || !(f.username || suggested)}
          style={{ justifyContent: 'center' }}>{busy ? (f.load_demo ? 'Setting up and adding the demo…' : 'Setting up…') : 'Create account'}</button>
        <p className="faint" style={{ margin: 0 }}>The built-in “admin” login is switched off once your account exists.</p>
      </form>
    </div>
  )
}

export default function Login({ onLogin }: { onLogin: (token: string, user: User) => void }) {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const [sso, setSso] = useState<{ enabled: boolean; name: string | null; password_login: boolean } | null>(null)
  useEffect(() => { api.get<{ needs_setup: boolean }>('/api/setup').then(r => setNeedsSetup(r.needs_setup)).catch(() => setNeedsSetup(false)) }, [])
  useEffect(() => {
    api.get<{ enabled: boolean; name: string | null; password_login: boolean }>('/api/auth/sso').then(setSso).catch(() => setSso(null))
    const m = /sso_error=([^&]*)/.exec(location.hash)
    if (m) { setErr(decodeURIComponent(m[1].replace(/\+/g, ' '))); history.replaceState(null, '', location.pathname + location.search) }
  }, [])
  const [showPw, setShowPw] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try {
      const r = await api.post<{ token: string; user: User }>('/api/auth/login', { username, password, remember })
      onLogin(r.token, r.user)
    } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }

  if (needsSetup === null) return null
  if (needsSetup) return <Setup onLogin={onLogin} />
  return (
    <div className="login">
      <form className="panel panel-pad login-card stack" onSubmit={submit}>
        <img src="/flask.svg" alt="" width={36} height={36} />
        <div>
          <h1>Benchtop</h1>
          <p className="muted" style={{ margin: 0 }}>Sign in to your lab notebook.</p>
        </div>
        {sso?.enabled && <>
          <a className="btn primary sso-btn" href={`/api/auth/sso/start?remember=${remember}`}>Sign in with {sso.name}</a>
          {(sso.password_login || showPw) ? <div className="or-rule"><span>or with a Benchtop password</span></div>
            : <button type="button" className="link-btn" style={{ alignSelf: 'center' }} onClick={() => setShowPw(true)}>Administrator sign-in</button>}
        </>}
        {(!sso?.enabled || sso.password_login || showPw) && <>
          <label className="field">Username<input className="input" autoFocus={!sso?.enabled} autoComplete="username" autoCapitalize="none" value={username} onChange={e => setUsername(e.target.value)} /></label>
          <label className="field">Password<input className="input" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label>
        </>}
        <label className="check-row">
          <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} /> Keep me signed in for 30 days
        </label>
        {err && <p className="error" role="alert">{err}</p>}
        {(!sso?.enabled || sso.password_login || showPw) &&
          <button className={`btn${sso?.enabled ? '' : ' primary'}`} disabled={busy || !username || !password} style={{ justifyContent: 'center' }}>Sign in</button>}
      </form>
    </div>
  )
}
