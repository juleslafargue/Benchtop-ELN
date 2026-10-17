import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../App'
import { api } from '../api'
import Modal from './Modal'
import { AudiencePicker, defaultAudience, type AudienceValue } from './Audience'

export default function NewNotebookDialog({ onClose }: { onClose: () => void }) {
  const { refreshNotebooks, toast, groups } = useApp()
  const nav = useNavigate()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [err, setErr] = useState('')
  const [aud, setAud] = useState<AudienceValue>(() => defaultAudience(groups))

  const create = async () => {
    try {
      const nb = await api.post<{ id: number }>('/api/notebooks', { name, description, ...aud })
      await refreshNotebooks()
      toast('Notebook created')
      onClose()
      nav(`/notebooks/${nb.id}`)
    } catch (e: any) { setErr(e.message) }
  }

  return (
    <Modal title="New notebook" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!name.trim()} onClick={create}>Create notebook</button>
    </>}>
      <div className="stack">
        <label className="field">Name<input className="input" autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Amide couplings" /></label>
        <label className="field">Description<textarea className="input" rows={2} value={description} onChange={e => setDescription(e.target.value)} /></label>
        <div className="field">Who can open it<AudiencePicker value={aud} onChange={setAud} /></div>
        {err && <p className="error">{err}</p>}
      </div>
    </Modal>
  )
}
