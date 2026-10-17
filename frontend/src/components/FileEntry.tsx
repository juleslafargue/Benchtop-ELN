import { useEffect, useState } from 'react'
import { api, downloadAuthed, type Attachment } from '../api'
import type { EntryEditorProps } from './EntryCard'
import { hydrogensInFormula, SPECTRUM_EXT } from '../spectra'
import SpectrumViewer, { type SavedAnalysis } from './SpectrumViewer'

function ImagePreview({ att }: { att: Attachment }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let u: string | null = null
    api.raw(`/api/attachments/${att.id}?inline=true`).then(r => r.blob()).then(b => { u = URL.createObjectURL(b); setUrl(u) }).catch(() => {})
    return () => { if (u) URL.revokeObjectURL(u) }
  }, [att.id])
  return url ? <img className="preview" src={url} alt={att.filename} /> : null
}

const size = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 1e3 ? `${(n / 1e3).toFixed(0)} kB` : `${n} B`)

export default function FileEntry({ content, onChange, readOnly, experimentId, attachments, onAttachmentsChanged, context }: EntryEditorProps) {
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [viewing, setViewing] = useState<number | null>(null)
  const ids: number[] = content.attachment_ids || []
  const mine = ids.map(id => attachments.find(a => a.id === id)).filter(Boolean) as Attachment[]

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true); setErr('')
    const added: number[] = []
    for (const f of Array.from(files)) {
      const fd = new FormData()
      fd.append('file', f)
      try { added.push((await api.post<Attachment>(`/api/experiments/${experimentId}/attachments`, fd)).id) }
      catch (e: any) { setErr(`${f.name}: ${e.message}`) }
    }
    onAttachmentsChanged()
    onChange({ ...content, attachment_ids: [...ids, ...added] })
    setBusy(false)
  }

  return (
    <div className="stack" style={{ gap: 8 }}>
      {!readOnly && (
        <label className={`dropzone${over ? ' over' : ''}`}
          onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
          onDrop={e => { e.preventDefault(); e.stopPropagation(); setOver(false); upload(e.dataTransfer.files) }}>
          {busy ? 'Uploading…' : 'Drop spectra, images or data files here, or click to choose'}
          <input type="file" multiple hidden onChange={e => upload(e.target.files)} />
        </label>
      )}
      {err && <p className="error">{err}</p>}
      {mine.length > 0 && (
        <ul className="files">
          {mine.map(a => (
            <li key={a.id} style={{ flexWrap: 'wrap' }}>
              <span className="grow"><b style={{ fontWeight: 600 }}>{a.filename}</b> <span className="faint">{size(a.size)} · sha256 {a.sha256.slice(0, 12)}…</span></span>
              {SPECTRUM_EXT.test(a.filename) && <button className={`btn sm${viewing === a.id ? ' primary' : ''}`} onClick={() => setViewing(viewing === a.id ? null : a.id)}>{viewing === a.id ? 'Hide spectrum' : 'View spectrum'}</button>}
              <button className="btn sm" onClick={() => downloadAuthed(`/api/attachments/${a.id}`, a.filename)}>Download</button>
              {!readOnly && <button className="btn ghost sm" onClick={() => onChange({ ...content, attachment_ids: ids.filter(i => i !== a.id) })}>Unlink</button>}
              {a.content_type.startsWith('image/') && <div style={{ flexBasis: '100%' }}><ImagePreview att={a} /></div>}
              {viewing === a.id && <div style={{ flexBasis: '100%' }}><SpectrumViewer attachmentId={a.id} filename={a.filename} expectedH={hydrogensInFormula(context.productFormula)} productSmiles={context.productSmiles} productMW={context.productMW}
                saved={(content.analyses || {})[a.id] as SavedAnalysis | undefined}
                onSave={readOnly ? undefined : an => onChange({ ...content, analyses: { ...(content.analyses || {}), [a.id]: an } })} /></div>}
            </li>
          ))}
        </ul>
      )}
      <input className="input" placeholder="Caption" disabled={readOnly} value={content.caption || ''}
        onChange={e => onChange({ ...content, caption: e.target.value })} />
    </div>
  )
}
