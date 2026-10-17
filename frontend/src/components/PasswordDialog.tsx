import { useState } from 'react'
import { useApp } from '../App'
import { api } from '../api'
import Modal from './Modal'

export default function PasswordDialog({ onClose }: { onClose: () => void }) {
  const { toast, user, setUser } = useApp()
  const first = user.has_password === false
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [err, setErr] = useState('')
  const save = async () => {
    try {
      await api.post('/api/auth/password', { current_password: cur, new_password: next })
      toast(first ? 'Signing password set' : 'Password changed'); setUser({ ...user, has_password: true }); onClose()
    }
    catch (e: any) { setErr(e.message) }
  }
  return (
    <Modal title={first ? 'Set a signing password' : 'Change password'} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={(!first && !cur) || next.length < 8} onClick={save}>{first ? 'Set password' : 'Change password'}</button></>}>
      <div className="stack">
        {first && <p style={{ margin: 0 }}>You sign in with single sign-on, but signing a record means re-entering a password (the e-signature rules ask for it). Choose one you'll remember; it's only used for signing.</p>}
        {!first && <label className="field">Current password<input className="input" type="password" autoComplete="current-password" autoFocus value={cur} onChange={e => setCur(e.target.value)} /></label>}
        <label className="field">New password (at least 8 characters)<input className="input" type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} /></label>
        {err && <p className="error">{err}</p>}
      </div>
    </Modal>
  )
}
