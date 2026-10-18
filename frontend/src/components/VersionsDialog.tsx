import { useEffect, useState } from 'react'
import { api, displayName, fmt, when, PURIFICATION_LABELS, type EntryKind, type UserBrief } from '../api'
import Modal from './Modal'
import CustomEntry from './CustomEntry'

interface V { version: number; title: string; content: any; deleted: boolean; created_at: string; created_by: UserBrief }

function Preview({ kind, content }: { kind: EntryKind; content: any }) {
  if (kind === 'custom') return <CustomEntry content={content} onChange={() => {}} readOnly experimentId={0} attachments={[]} onAttachmentsChanged={() => {}}
    context={{ productFormula: null, productSmiles: null, productMW: null, openAttachmentId: null }} />
  if (kind === 'text') return <div className="prose" style={{ fontSize: 15 }} dangerouslySetInnerHTML={{ __html: content.html || '<p class="faint">(empty)</p>' }} />
  if (kind === 'reaction') return (
    <table className="plain"><thead><tr><th>Role</th><th>Name</th><th>Equiv</th><th>mmol</th><th>Mass (mg)</th><th>Yield</th></tr></thead>
      <tbody>{(content.rows || []).map((r: any, i: number) => (
        <tr key={i}><td>{r.role}</td><td>{r.name}</td><td className="num">{fmt(r.equiv)}</td><td className="num">{fmt(r.mmol)}</td>
          <td className="num">{fmt(r.mass_mg ?? r.actual_mass_mg)}</td><td className="num">{r.yield_pct ? `${r.yield_pct}%` : ''}</td></tr>))}</tbody></table>
  )
  if (kind === 'table') return (
    <table className="plain"><thead><tr>{(content.columns || []).map((c: any, i: number) => <th key={i}>{c.name}</th>)}</tr></thead>
      <tbody>{(content.rows || []).map((r: string[], i: number) => <tr key={i}>{r.map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody></table>
  )
  if (kind === 'purification') return (
    <div><p>{PURIFICATION_LABELS[content.method] || content.method}{content.eluent ? `, ${content.eluent}` : ''}{content.rf ? ` · Rf ${content.rf}` : ''}{content.isolated_mg != null ? ` · ${content.isolated_mg} mg` : ''}</p>
      <div className="prose" style={{ fontSize: 15 }} dangerouslySetInnerHTML={{ __html: content.notes_html || '' }} /></div>
  )
  if (kind === 'characterization') return (
    <table className="plain"><tbody>{(content.items || []).map((it: any, i: number) => (
      <tr key={i}><td><b>{it.technique}</b> {it.conditions}</td><td>{it.data}</td></tr>))}</tbody></table>
  )
  return <p className="faint">{(content.attachment_ids || []).length} linked file(s). {content.caption}</p>
}

export default function VersionsDialog({ entryId, kind, onClose }: { entryId: number; kind: EntryKind; onClose: () => void }) {
  const [vs, setVs] = useState<V[]>([])
  const [sel, setSel] = useState(0)
  useEffect(() => { api.get<V[]>(`/api/entries/${entryId}/versions`).then(setVs) }, [entryId])
  const v = vs[sel]
  return (
    <Modal wide title="Section history" onClose={onClose} footer={<button className="btn" onClick={onClose}>Close</button>}>
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 18, minHeight: 300 }}>
        <ul className="list" style={{ maxHeight: 460, overflowY: 'auto', borderRight: '1px solid var(--rule)' }}>
          {vs.map((x, i) => (
            <li key={x.version}>
              <button className={`btn ghost${i === sel ? ' primary' : ''}`} style={{ width: '100%', justifyContent: 'flex-start', borderRadius: 0, flexDirection: 'column', alignItems: 'flex-start' }} onClick={() => setSel(i)}>
                <span>v{x.version}{x.deleted ? ' (removed)' : ''}</span>
                <span style={{ fontSize: 12, opacity: .8 }}>{when(x.created_at)} · {displayName(x.created_by)}</span>
              </button>
            </li>
          ))}
        </ul>
        <div style={{ overflow: 'auto', maxHeight: 460 }}>
          {v && <><h3 style={{ fontSize: 16, marginBottom: 10 }}>{v.title}</h3><Preview kind={kind} content={v.content} /></>}
        </div>
      </div>
    </Modal>
  )
}
