import { useEffect, useState } from 'react'
import { api } from '../api'
import Modal from './Modal'

interface Draft { text: string; html: string; missing: string[] }

export default function WriteupDialog({ experimentId, canInsert, onInserted, onClose }: {
  experimentId: number; canInsert: boolean; onInserted: () => void; onClose: () => void
}) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState(false)
  useEffect(() => { api.get<Draft>(`/api/experiments/${experimentId}/writeup`).then(setDraft).catch(e => setErr(e.message)) }, [experimentId])

  const copy = async () => {
    if (!draft) return
    try {
      // rich text for Word/Pages/Docs, plain text everywhere else
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([draft.html], { type: 'text/html' }),
        'text/plain': new Blob([draft.text], { type: 'text/plain' }),
      })])
    } catch {
      try { await navigator.clipboard.writeText(draft.text) } catch { /* clipboard blocked */ }
    }
    setCopied(true); setTimeout(() => setCopied(false), 1500)
  }
  const insert = async () => {
    if (!draft) return
    await api.post(`/api/experiments/${experimentId}/entries`, { kind: 'text', title: 'Experimental (SI draft)', content: { html: draft.html } })
    onInserted()
    onClose()
  }

  return (
    <Modal wide title="Draft experimental write-up" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Close</button>
      <button className="btn" disabled={!draft} onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
      {canInsert && <button className="btn primary" disabled={!draft} onClick={insert}>Add as a section</button>}
    </>}>
      {err && <p className="error">{err}</p>}
      {!draft && !err && <p className="faint">Composing…</p>}
      {draft && <>
        <p className="faint" style={{ marginTop: 0 }}>Built from this experiment's reaction table, conditions, work-up and characterisation. Nothing is guessed; check it before you use it.</p>
        <div className="prose panel panel-pad" style={{ fontSize: 16 }} dangerouslySetInnerHTML={{ __html: draft.html }} />
        {draft.missing.length > 0 && <p className="notice" style={{ marginTop: 12 }}>
          Not in the record yet, so left out: {draft.missing.join(', ')}.</p>}
      </>}
    </Modal>
  )
}
