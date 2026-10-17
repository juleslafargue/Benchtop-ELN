import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../App'
import { api, type ExperimentSummary } from '../api'
import Modal from './Modal'

export default function NewExperimentDialog({ notebookId, onClose }: { notebookId?: number; onClose: () => void }) {
  const { notebooks, refreshNotebooks, user } = useApp()
  const nav = useNavigate()
  const [title, setTitle] = useState('')
  const pref = user.preferences?.default_notebook_id
  const [nb, setNb] = useState<number | ''>(notebookId ?? (notebooks.some(n => n.id === pref) ? pref! : notebooks[0]?.id) ?? '')
  const [tpl, setTpl] = useState<number | ''>('')
  const [templates, setTemplates] = useState<ExperimentSummary[]>([])
  const [err, setErr] = useState('')

  useEffect(() => { api.get<ExperimentSummary[]>('/api/experiments?templates=true').then(setTemplates) }, [])

  const create = async () => {
    try {
      const e = await api.post<ExperimentSummary>('/api/experiments', {
        title, notebook_id: nb || null, template_id: tpl || null,
      })
      await refreshNotebooks()
      onClose()
      nav(`/experiments/${e.id}`)
    } catch (e: any) { setErr(e.message) }
  }

  return (
    <Modal title="New experiment" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!title.trim() || !nb} onClick={create}>Create experiment</button>
    </>}>
      <div className="stack">
        <label className="field">Title<input className="input" autoFocus value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Boc protection of 4-aminopiperidine" /></label>
        <label className="field">Notebook
          <select className="select" value={nb} onChange={e => setNb(Number(e.target.value))}>
            {notebooks.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
          </select>
        </label>
        {!notebooks.length && <p className="error">Create a notebook first.</p>}
        <label className="field">Start from
          <select className="select" value={tpl} onChange={e => setTpl(e.target.value ? Number(e.target.value) : '')}>
            <option value="">Standard sections (aim, reaction, procedure, results)</option>
            {templates.map(t => <option key={t.id} value={t.id}>Template: {t.title}</option>)}
          </select>
        </label>
        {err && <p className="error">{err}</p>}
      </div>
    </Modal>
  )
}
