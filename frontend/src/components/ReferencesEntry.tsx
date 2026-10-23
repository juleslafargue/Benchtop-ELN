import { useState } from 'react'
import { api } from '../api'
import type { EntryEditorProps } from './EntryCard'

export interface Reference {
  doi: string; title: string; authors: string[]; journal: string; year: string
  volume: string; issue: string; pages: string; note: string
}

const EMPTY: Reference = { doi: '', title: '', authors: [], journal: '', year: '', volume: '', issue: '', pages: '', note: '' }

/** The DOI in whatever was pasted ("doi:10.…", "https://doi.org/10.…", a trailing full stop…), or ''. Same rules as the server. */
export function normaliseDoi(text: string): string {
  let s = (text || '').trim().replace(/^<|>$/g, '').trim()
  s = s.replace(/^(doi:\s*|(https?:\/\/)?(dx\.)?doi\.org\/)/i, '').trim()
  if (s.includes('%')) { try { s = decodeURIComponent(s) } catch { /* keep as typed */ } }
  s = s.replace(/[.,;]+$/, '')
  if (s.endsWith(')') && (s.match(/\)/g) || []).length > (s.match(/\(/g) || []).length) s = s.slice(0, -1)
  return s.length <= 300 && /^10\.\d{4,9}\/\S+$/.test(s) ? s : ''
}

const doiLink = (r: Reference) => { const d = normaliseDoi(r.doi); return d ? `https://doi.org/${encodeURI(d)}` : '' }
const enDash = (p: string) => p.replace(/--/g, '–').replace(/(\w)-(\w)/g, '$1–$2')

/** Authors. Title. Journal Year, Volume (Issue), Pages. (ACS style, as in a paper's SI) */
export function Citation({ r }: { r: Reference }) {
  const authors = (r.authors || []).join('; ').replace(/\.$/, '')
  const src = [r.journal && <i key="j">{r.journal}</i>, r.year && <b key="y">{r.year}</b>].filter(Boolean)
  const vol = [r.volume && <i key="v">{r.volume}</i>, r.issue && <span key="i"> ({r.issue})</span>].filter(Boolean)
  const tail: React.ReactNode[] = []
  if (src.length) tail.push(<span key="s">{src.flatMap((x, i) => (i ? [' ', x] : [x]))}</span>)
  if (vol.length) tail.push(<span key="v">{vol}</span>)
  if (r.pages) tail.push(<span key="p">{enDash(r.pages)}</span>)
  const link = doiLink(r)
  return (
    <span className="ref-cite">
      {authors && <>{authors}. </>}
      {r.title && <span className="cite-title">{r.title.replace(/\.$/, '')}. </span>}
      {tail.length > 0 && <>{tail.flatMap((x, i) => (i ? [', ', x] : [x]))}. </>}
      {link ? <a href={link} target="_blank" rel="noopener noreferrer" className="ref-doi">{link}</a>
        : r.doi ? <span className="ref-doi">{r.doi}</span> : null}
      {!authors && !r.title && !tail.length && !r.doi && <span className="faint">(empty reference)</span>}
    </span>
  )
}

function RefForm({ r, onChange, onDone }: { r: Reference; onChange: (r: Reference) => void; onDone: () => void }) {
  // kept here as typed: the saved list drops blank lines, which would swallow the Enter just pressed
  const [authors, setAuthors] = useState((r.authors || []).join('\n'))
  const f = (k: keyof Reference, label: string, cls = '') => (
    <label className={`fld ${cls}`}><span className="fld-cap">{label}</span>
      <input className="input" value={(r[k] as string) || ''} onChange={e => onChange({ ...r, [k]: e.target.value })} /></label>
  )
  return (
    <div className="ref-form">
      {f('doi', 'DOI', 'w-wide')}
      {f('title', 'Title', 'w-full')}
      <label className="fld w-full"><span className="fld-cap">Authors (one per line, family name first)</span>
        <textarea className="input" rows={Math.min(6, Math.max(2, authors.split('\n').length))} value={authors}
          placeholder={'Gottlieb, Hugo E.\nKotlyar, Vadim'}
          onChange={e => { setAuthors(e.target.value); onChange({ ...r, authors: e.target.value.split('\n').map(a => a.trim()).filter(Boolean) }) }} /></label>
      {f('journal', 'Journal', 'w-wide')}
      {f('year', 'Year')}
      {f('volume', 'Volume')}
      {f('issue', 'Issue')}
      {f('pages', 'Pages')}
      <label className="fld w-full"><span className="fld-cap">Note</span>
        <textarea className="input" rows={2} value={r.note || ''} placeholder="What you used from this paper (optional)"
          onChange={e => onChange({ ...r, note: e.target.value })} /></label>
      <div className="row w-full"><button className="btn sm" onClick={onDone}>Done</button></div>
    </div>
  )
}

export default function ReferencesEntry({ content, onChange, readOnly }: EntryEditorProps) {
  const items: Reference[] = (content.items || []).map((r: any) => ({ ...EMPTY, ...r, authors: Array.isArray(r?.authors) ? r.authors : [] }))
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ text: string; byHand?: string } | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const set = (next: Reference[]) => onChange({ ...content, items: next })

  const addByHand = (doi = '') => {
    set([...items, { ...EMPTY, doi }])
    setEditing(items.length); setMsg(null); setQ('')
  }

  const lookUp = async () => {
    const doi = normaliseDoi(q)
    if (!doi) { setMsg({ text: q.trim() ? 'That doesn’t look like a DOI. A DOI starts with 10. and has a slash, for example 10.1021/jacs.9b13580' : 'Paste a DOI first' }); return }
    const dup = items.findIndex(r => normaliseDoi(r.doi).toLowerCase() === doi.toLowerCase())
    if (dup >= 0) { setMsg({ text: `This paper is already in the list (number ${dup + 1}).` }); return }
    setBusy(true); setMsg(null)
    try {
      const r = await api.get<Reference>(`/api/doi?q=${encodeURIComponent(doi)}`)
      set([...items, { ...EMPTY, ...r }])
      setQ('')
    } catch (e: any) {
      setMsg({ text: e.message, byHand: doi })
    } finally { setBusy(false) }
  }

  const move = (i: number, d: -1 | 1) => {
    const next = [...items]; [next[i], next[i + d]] = [next[i + d], next[i]]
    set(next); if (editing === i) setEditing(i + d)
  }

  if (readOnly && !items.length) return <p className="faint">No references.</p>
  return (
    <div className="stack" style={{ gap: 10 }}>
      {items.length > 0 && <ol className="ref-list">
        {items.map((r, i) => (
          <li key={i} className={`ref-item${editing === i ? ' editing' : ''}`}>
            {editing === i && !readOnly
              ? <RefForm r={r} onChange={nr => set(items.map((x, j) => (j === i ? nr : x)))}
                  onDone={() => setEditing(null)} />
              : <div className="ref-row">
                  <div className="ref-text"><Citation r={r} />{r.note && <div className="ref-note">{r.note}</div>}</div>
                  {!readOnly && <div className="ref-actions">
                    <button className="btn ghost sm" onClick={() => setEditing(i)} aria-label={`Edit reference ${i + 1}`}>Edit</button>
                    <button className="btn ghost sm" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move reference ${i + 1} up`}>↑</button>
                    <button className="btn ghost sm" disabled={i === items.length - 1} onClick={() => move(i, 1)} aria-label={`Move reference ${i + 1} down`}>↓</button>
                    <button className="btn ghost sm" onClick={() => { set(items.filter((_, j) => j !== i)); if (editing !== null && editing >= i) setEditing(null) }}
                      aria-label={`Remove reference ${i + 1}`}>×</button>
                  </div>}
                </div>}
          </li>
        ))}
      </ol>}
      {!readOnly && <>
        <form className="ref-add" onSubmit={e => { e.preventDefault(); lookUp() }}>
          <input className="input" value={q} onChange={e => { setQ(e.target.value); setMsg(null) }} disabled={busy}
            placeholder="Paste a DOI, e.g. 10.1021/jo971176v" aria-label="DOI to look up" />
          <button className="btn sm primary" type="submit" disabled={busy}>{busy ? 'Looking up…' : 'Add by DOI'}</button>
          <button className="btn ghost sm" type="button" onClick={() => addByHand()}>Type one in</button>
        </form>
        {msg && <p className="error" role="alert">{msg.text}{msg.byHand !== undefined && <> <button className="btn sm" onClick={() => addByHand(msg.byHand)}>Type it in</button></>}</p>}
        {!items.length && !msg && <p className="faint">Benchtop looks up the title, authors and journal at doi.org.</p>}
      </>}
    </div>
  )
}
