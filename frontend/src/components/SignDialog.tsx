import { useState } from 'react'
import { api, type Experiment } from '../api'
import Modal from './Modal'

export type SignAction = 'sign' | 'witness' | 'reject' | 'withdraw' | 'void'

const COPY: Record<SignAction, { title: string; button: string; meaning: string; needsReason?: boolean; danger?: boolean }> = {
  sign: { title: 'Sign as author', button: 'Sign and lock', meaning: 'I am the author of this record and its content is accurate and complete. Signing locks it for witnessing.' },
  witness: { title: 'Witness this record', button: 'Witness', meaning: 'I have read and understood this record. Witnessing makes it final.' },
  reject: { title: 'Return to author', button: 'Return to author', meaning: 'The record goes back to the author as open so it can be corrected. Your reason is kept in the record.', needsReason: true, danger: true },
  withdraw: { title: 'Withdraw signature', button: 'Withdraw', meaning: 'Unlocks the record so you can edit it again. Your earlier signature stays in the history.' },
  void: { title: 'Void experiment', button: 'Void', meaning: 'Marks the record as withdrawn. Nothing is deleted; the record and your reason remain visible.', needsReason: true, danger: true },
}

export default function SignDialog({ exp, action, onDone, onClose }: {
  exp: Experiment; action: SignAction; onDone: (e: Experiment) => void; onClose: () => void
}) {
  const c = COPY[action]
  const [password, setPassword] = useState('')
  const [comment, setComment] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const go = async () => {
    setBusy(true); setErr('')
    try { onDone(await api.post<Experiment>(`/api/experiments/${exp.id}/${action}`, { password, comment })) }
    catch (e: any) { setErr(e.message); setBusy(false) }
  }

  return (
    <Modal title={c.title} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className={`btn ${c.danger ? 'danger' : 'primary'}`} disabled={busy || !password || (c.needsReason && !comment.trim())} onClick={go}>{c.button}</button>
    </>}>
      <form className="stack" onSubmit={e => { e.preventDefault(); go() }}>
        <p style={{ margin: 0 }}><b>{exp.eid}</b> {exp.title}</p>
        <p className="notice" style={{ margin: 0 }}>{c.meaning}</p>
        <label className="field">{c.needsReason ? 'Reason (required)' : 'Comment (optional)'}
          <textarea className="input" rows={2} value={comment} onChange={e => setComment(e.target.value)} /></label>
        <label className="field">Re-enter your password to apply your electronic signature
          <input className="input" type="password" autoComplete="current-password" autoFocus value={password} onChange={e => setPassword(e.target.value)} /></label>
        {err && <p className="error" role="alert">{err}</p>}
        <button hidden />
      </form>
    </Modal>
  )
}
