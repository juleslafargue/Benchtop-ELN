import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useApp } from '../App'
import { api, displayName, downloadAuthed, when, type AuditEvent, type EntryKind, type Experiment, type ExperimentSummary, type Signature } from '../api'
import EntryCard from '../components/EntryCard'
import { AddSection } from '../components/AddSection'
import SignDialog, { type SignAction } from '../components/SignDialog'
import WriteupDialog from '../components/WriteupDialog'
import ScaleDialog from '../components/ScaleDialog'
import { useBackTarget } from '../components/BackButton'
import { parseSpectrumBuffer, techniqueFor } from '../spectra'
import type { Attachment, Entry, User } from '../api'

const STAMP_WORD = { open: 'Draft', signed: 'Signed', witnessed: 'Witnessed', voided: 'Void' }

function Stamp({ exp, fresh }: { exp: Experiment; fresh: boolean }) {
  const sigs = exp.signatures.filter(s => ['author', 'witness', 'void'].includes(s.meaning))
  const last = sigs[sigs.length - 1]
  return (
    <div className={`stamp ${exp.status}${fresh ? ' fresh' : ''}`} role="img" aria-label={`Status: ${exp.status}`}>
      <span className="word">{STAMP_WORD[exp.status]}</span>
      <span className="by">{exp.status === 'open' ? 'editable' : last ? `${last.signer_name || displayName(last.user)} · ${new Date(last.signed_at).toLocaleDateString()}` : ''}</span>
    </div>
  )
}

const SIG_LABEL: Record<string, string> = { author: 'Signed by author', witness: 'Witnessed', rejected: 'Returned to author', withdrawn: 'Signature withdrawn', void: 'Voided' }

function SignatureRail({ sigs }: { sigs: Signature[] }) {
  if (!sigs.length) return <p className="faint">No signatures yet.</p>
  return <>{sigs.map(s => (
    <div key={s.id} className={`sig ${s.meaning}`}>
      <div style={{ fontWeight: 600 }}>{SIG_LABEL[s.meaning] || s.meaning}</div>
      <div className="faint">{s.signer_name || displayName(s.user)} · {when(s.signed_at)}</div>
      {s.signer_name && s.user && s.signer_name !== (s.user.full_name || s.user.username) && <div className="faint">now {displayName(s.user)}</div>}
      {s.comment && <div style={{ fontSize: 13 }}>“{s.comment}”</div>}
      <div className="hash" title={s.content_hash}>{s.content_hash.slice(0, 24)}…</div>
    </div>
  ))}</>
}

function AuditTab({ expId }: { expId: number }) {
  const [events, setEvents] = useState<AuditEvent[]>([])
  useEffect(() => { api.get<AuditEvent[]>(`/api/experiments/${expId}/audit`).then(setEvents) }, [expId])
  const describe = (e: AuditEvent) => {
    const d = e.details || {}
    if (e.object_type === 'entry') return `${e.action} section${d.title ? ` “${d.title}”` : ''}${d.version ? ` → v${d.version}` : ''}`
    if (e.object_type === 'attachment') return `uploaded ${d.filename}`
    if (e.action.startsWith('sign:')) return `${e.action.slice(5)} signature${d.comment ? ` — “${d.comment}”` : ''}`
    if (e.action === 'update') return `changed ${Object.keys(d.after || {}).join(', ')}`
    return e.action
  }
  return (
    <ul className="timeline">
      {events.map(e => (
        <li key={e.id}>
          <span className="faint">{when(e.ts)}</span>
          <span>{e.username}</span>
          <span><span className="act">{describe(e)}</span> <span className="hash" title={e.hash}>#{e.id} {e.hash.slice(0, 10)}</span></span>
        </li>
      ))}
    </ul>
  )
}

export default function ExperimentPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const { user, canWrite, toast, refreshNotebooks, setUser } = useApp()
  const [exp, setExp] = useState<Experiment | null>(null)
  const [err, setErr] = useState('')
  const [tab, setTab] = useState<'record' | 'history'>('record')
  const [dialog, setDialog] = useState<SignAction | null>(null)
  const [fresh, setFresh] = useState(false)
  const [verify, setVerify] = useState<{ intact: boolean; content_hash: string } | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [writeup, setWriteup] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [importing, setImporting] = useState(false)
  const [justAdded, setJustAdded] = useState<number | null>(null)
  const [importNotes, setImportNotes] = useState<string[]>([])
  const [scaling, setScaling] = useState(false)
  const dragDepth = useRef(0)

  const load = useCallback(async () => {
    try { setExp(await api.get<Experiment>(`/api/experiments/${id}`)); setErr('') } catch (e: any) { setErr(e.message) }
  }, [id])
  useEffect(() => { load(); setVerify(null); setTab('record') }, [load])

  useBackTarget(exp?.notebook_id ? `/notebooks/${exp.notebook_id}` : exp?.is_template ? '/templates' : null,
    exp?.notebook_name || (exp?.is_template ? 'Templates' : 'Dashboard'))
  if (err) return <p className="empty">{err}</p>
  if (!exp) return null

  const rxnRows: any[] = exp.entries.find(e => e.kind === 'reaction' && e.content?.rows?.length)?.content.rows || []
  const product = rxnRows.filter(r => r.role === 'product').sort((a, b) => (b.yield_pct ?? -1) - (a.yield_pct ?? -1))[0]
  const context = { productFormula: product?.formula || null, productSmiles: product?.smiles || null, productMW: product?.mw ?? null, openAttachmentId: justAdded }

  /** Drop files anywhere: spectra become characterisation items (technique and conditions read from
   *  the file); everything else goes into a Files section. */
  const importFiles = async (files: File[]) => {
    if (!files.length) return
    setImporting(true)
    const added: string[] = []
    const notes: string[] = []
    let firstSpectrum: number | null = null
    try {
      for (const f of files) {
        let spec = null
        try { spec = await parseSpectrumBuffer(await f.arrayBuffer(), f.name) } catch (e: any) {
          // explain why a zip of NMR data wasn't read; other non-spectrum files are just files
          if (/\.zip$/i.test(f.name) && /FID|pdata|JCAMP/.test(e?.message || '')) notes.push(`${f.name}: ${e.message}`)
        }
        const fd = new FormData(); fd.append('file', f)
        const att = await api.post<Attachment>(`/api/experiments/${exp.id}/attachments`, fd)
        const fresh = await api.get<Experiment>(`/api/experiments/${exp.id}`)
        if (spec) {
          const { technique, conditions } = techniqueFor(spec)
          const ch = fresh.entries.find(e => e.kind === 'characterization')
          const item = { technique, conditions, data: '', attachment_id: att.id }
          if (ch) {
            const items = [...(ch.content.items || [])]
            const slot = items.findIndex((it: any) => !it.attachment_id && !it.data && it.technique.replace(/\s/g, '').toLowerCase() === technique.replace(/\s/g, '').toLowerCase())
            if (slot >= 0) items[slot] = { ...items[slot], attachment_id: att.id, conditions: items[slot].conditions || conditions }
            else items.push(item)
            await api.put<Entry>(`/api/entries/${ch.id}`, { content: { ...ch.content, items }, expected_version: ch.version })
          } else {
            await api.post(`/api/experiments/${exp.id}/entries`, { kind: 'characterization', title: 'Characterisation', content: { items: [item] } })
          }
          firstSpectrum = firstSpectrum ?? att.id
          added.push(`${technique} (${f.name})`)
        } else {
          const fe = fresh.entries.find(e => e.kind === 'file')
          if (fe) await api.put<Entry>(`/api/entries/${fe.id}`, { content: { ...fe.content, attachment_ids: [...(fe.content.attachment_ids || []), att.id] }, expected_version: fe.version })
          else await api.post(`/api/experiments/${exp.id}/entries`, { kind: 'file', title: 'Spectra & files', content: { attachment_ids: [att.id], caption: '' } })
          added.push(f.name)
        }
      }
      setJustAdded(firstSpectrum)
      await load()
      setReloadKey(k => k + 1)
      toast(`Added ${added.join(', ')}`)
      if (notes.length) setImportNotes(notes)
    } catch (e: any) { toast(e.message) } finally { setImporting(false) }
  }

  const isOwner = exp.owner.id === user.id
  const editable = canWrite && exp.status === 'open' && (isOwner || user.role === 'admin' || exp.is_template)

  const patch = async (body: Partial<Experiment>) => {
    try { const r = await api.patch<ExperimentSummary>(`/api/experiments/${exp.id}`, body); setExp({ ...exp, ...r }) }
    catch (e: any) { toast(e.message) }
  }
  // insert before section `before` (an index into exp.entries), or at the end; then bring it into view
  const addSection = async (kind: EntryKind, before?: number) => {
    const titles = { text: 'Notes', reaction: 'Reaction', table: 'Data', file: 'Spectra & files', purification: 'Work-up & purification', characterization: 'Characterisation', safety: 'Safety & risk assessment', screen: 'Screening' }
    try {
      const position = before != null && exp.entries[before] ? exp.entries[before].position : undefined
      const e = await api.post<Entry>(`/api/experiments/${exp.id}/entries`, { kind, title: titles[kind], ...(position != null ? { position } : {}) })
      await load()
      requestAnimationFrame(() => {
        const el = document.getElementById(`entry-${e.id}`)
        if (!el) return
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
        el.classList.add('just-added'); setTimeout(() => el.classList.remove('just-added'), 1600)
      })
    } catch (err: any) { toast(err.message) }
  }
  const kindsPresent = exp.entries.map(e => e.kind)
  const move = async (idx: number, dir: -1 | 1) => {
    const ids = exp.entries.map(e => e.id)
    ;[ids[idx], ids[idx + dir]] = [ids[idx + dir], ids[idx]]
    await api.post(`/api/experiments/${exp.id}/reorder`, { entry_ids: ids })
    await load()
    setReloadKey(k => k + 1)
  }
  const scaleUp = async (factor: number) => {
    try {
      const r = await api.post<ExperimentSummary>(`/api/experiments/${exp.id}/clone?scale=${factor}`)
      await refreshNotebooks(); setScaling(false)
      toast(`New experiment at ×${factor} scale`)
      nav(`/experiments/${r.id}`)
    } catch (e: any) { toast(e.message) }
  }
  const clone = async (asTemplate: boolean) => {
    const r = await api.post<ExperimentSummary>(`/api/experiments/${exp.id}/clone?as_template=${asTemplate}`)
    await refreshNotebooks()
    toast(asTemplate ? 'Saved as template' : 'Copy created')
    nav(`/experiments/${r.id}`)
  }
  const runVerify = async () => setVerify(await api.get(`/api/experiments/${exp.id}/verify`))
  const attachmentsChanged = async () => {
    const r = await api.get<Experiment>(`/api/experiments/${exp.id}`)
    setExp(e => (e ? { ...e, attachments: r.attachments } : e))
  }

  return (
    <article
      onDragEnter={e => { if (editable && e.dataTransfer.types.includes('Files')) { dragDepth.current++; setDragging(true) } }}
      onDragLeave={() => { if (--dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false) } }}
      onDragOver={e => { if (editable && e.dataTransfer.types.includes('Files')) e.preventDefault() }}
      onDrop={e => {
        dragDepth.current = 0; setDragging(false)
        if (!editable || e.isDefaultPrevented()) return // a section's own drop zone already handled it
        e.preventDefault(); importFiles(Array.from(e.dataTransfer.files))
      }}>
      {(dragging || importing) && <div className="drop-overlay"><div>
        {importing ? 'Adding files…' : <>Drop to add to <b>{exp.eid}</b><br /><span className="faint" style={{ fontSize: 14 }}>
          Spectra (JCAMP-DX, Bruker .zip, CSV) go into Characterisation with the technique filled in; other files go into a Files section.</span></>}
      </div></div>}
      <header className="exp-head">
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 8 }}>
            <span className="eid">{exp.eid}</span>
            {exp.notebook_id && <Link className="faint" to={`/notebooks/${exp.notebook_id}`}>{exp.notebook_name}</Link>}
            {exp.is_template && <span className="tag">Template</span>}
          </div>
          <input className="exp-title-input" aria-label="Experiment title" disabled={!editable} defaultValue={exp.title} key={`t${exp.id}`}
            onBlur={e => e.target.value.trim() && e.target.value !== exp.title && patch({ title: e.target.value })} />
          <div className="meta-line">
            <span>Author: {displayName(exp.owner)}</span>
            <span>Created {when(exp.created_at)}</span>
            <span>Modified {when(exp.updated_at)}</span>
          </div>
          <div className="row" style={{ marginTop: 8, gap: 6 }}>
            {exp.tags.map(t => <span className="tag" key={t}>{t}{editable && <button className="btn ghost sm" style={{ padding: '0 0 0 4px' }} aria-label={`Remove tag ${t}`} onClick={() => patch({ tags: exp.tags.filter(x => x !== t) })}>×</button>}</span>)}
            {editable && <input className="input" style={{ width: 110, padding: '1px 8px', fontSize: 13 }} placeholder="Add tag" aria-label="Add tag"
              onKeyDown={e => { const v = (e.target as HTMLInputElement).value.trim(); if (e.key === 'Enter' && v) { patch({ tags: [...exp.tags, v] }); (e.target as HTMLInputElement).value = '' } }} />}
          </div>
        </div>
        {!exp.is_template && <Stamp exp={exp} fresh={fresh} />}
      </header>

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'record'} onClick={() => setTab('record')}>Record</button>
        <button role="tab" aria-selected={tab === 'history'} onClick={() => setTab('history')}>Audit trail</button>
        {tab === 'record' && editable && <div className="tabs-end"><AddSection variant="button" existing={kindsPresent} onAdd={k => addSection(k)} /></div>}
      </div>

      {tab === 'history' ? <AuditTab expId={exp.id} /> : (
        <div className="layout-2">
          <div>
            {importNotes.length > 0 && <div className="notice" style={{ marginBottom: 14 }}>
              {importNotes.map(n => <div key={n}>{n}</div>)}
              <button className="btn ghost sm" onClick={() => setImportNotes([])}>Dismiss</button></div>}
            {exp.status === 'signed' && <p className="notice">This record is signed and locked. {isOwner ? 'Withdraw your signature to make changes.' : 'Review it, then witness it or return it to the author.'}</p>}
            {exp.status === 'voided' && <p className="notice">This record has been voided. It is kept for reference.</p>}
            <div style={{ marginTop: exp.status === 'open' ? 0 : 14 }}>
              {exp.entries.map((e, i) => (
                <Fragment key={`${e.id}-${reloadKey}`}>
                  {i > 0 && (editable
                    ? <AddSection variant="between" existing={kindsPresent} onAdd={k => addSection(k, i)} label="Add section here" />
                    : <div className="entry-gap" />)}
                  <EntryCard entry={e} readOnly={!editable} experimentId={exp.id} context={context}
                    attachments={exp.attachments} onAttachmentsChanged={attachmentsChanged}
                    onDeleted={load} onMove={d => move(i, d)} isFirst={i === 0} isLast={i === exp.entries.length - 1} />
                </Fragment>
              ))}
            </div>
            {editable && <AddSection variant="end" existing={kindsPresent} onAdd={k => addSection(k)} label={exp.entries.length ? 'Add a section' : 'Add the first section'} />}
          </div>

          <aside className="rail">
            {!exp.is_template && canWrite && (
              <div className="stack" style={{ gap: 8 }}>
                {exp.status === 'open' && isOwner && <button className="btn primary" onClick={() => setDialog('sign')}>Sign as author</button>}
                {exp.status === 'signed' && !isOwner && <>
                  <button className="btn primary" onClick={() => setDialog('witness')}>Witness</button>
                  <button className="btn" onClick={() => setDialog('reject')}>Return to author</button>
                </>}
                {exp.status === 'signed' && isOwner && <button className="btn" onClick={() => setDialog('withdraw')}>Withdraw signature</button>}
              </div>
            )}
            {!exp.is_template && <div>
              <h3>Signatures</h3>
              <SignatureRail sigs={exp.signatures} />
              <button className="btn ghost sm" onClick={runVerify}>Verify integrity</button>
              {verify && <p style={{ fontSize: 13, margin: '6px 0 0' }} className={verify.intact ? '' : 'error'}>
                {verify.intact ? 'Content matches the latest signature.' : 'Content does not match the signature. The record may have been altered outside the app.'}
                <span className="hash" style={{ display: 'block' }}>{verify.content_hash}</span></p>}
            </div>}
            <div className="stack" style={{ gap: 6 }}>
              <h3>Record</h3>
              {!exp.is_template && (() => {
                const pins = user.preferences?.pinned || []
                const pinned = pins.includes(exp.id)
                return <button className={`btn sm${pinned ? ' primary' : ''}`} aria-pressed={pinned} onClick={async () => {
                  try { setUser(await api.patch<User>('/api/auth/me', { preferences: { pinned: pinned ? pins.filter(i => i !== exp.id) : [exp.id, ...pins] } })); toast(pinned ? 'Unpinned' : 'Pinned to your dashboard') }
                  catch (e: any) { toast(e.message) }
                }}>{pinned ? 'Pinned to dashboard' : 'Pin to dashboard'}</button>
              })()}
              {!exp.is_template && <button className="btn sm" onClick={() => setWriteup(true)}>Draft write-up</button>}
              <button className="btn sm" onClick={() => downloadAuthed(`/api/experiments/${exp.id}/export`, `${exp.eid}.html`, true)}>Open printable report</button>
              <button className="btn sm" onClick={() => downloadAuthed(`/api/experiments/${exp.id}/export?format=md`, `${exp.eid} ${exp.title.replace(/[\\/:*?"<>|#^[\]]+/g, ' ').slice(0, 80)}.md`)}>Download Markdown</button>
              <button className="btn sm" onClick={() => downloadAuthed(`/api/experiments/${exp.id}/export?format=json`, `${exp.eid}.json`)}>Download JSON</button>
              {canWrite && <button className="btn sm" onClick={() => clone(false)}>Duplicate</button>}
              {canWrite && !exp.is_template && <button className="btn sm" onClick={() => setScaling(true)}>Repeat at another scale…</button>}
              {canWrite && !exp.is_template && <button className="btn sm" onClick={() => clone(true)}>Save as template</button>}
              {canWrite && (isOwner || user.role === 'admin') && exp.status !== 'voided' && !exp.is_template &&
                <button className="btn ghost sm danger" onClick={() => setDialog('void')}>Void…</button>}
            </div>
          </aside>
        </div>
      )}

      {scaling && (() => {
        const rx = exp.entries.find(e => e.kind === 'reaction')?.content
        const lim = (rx?.rows || []).find((r: any) => r.limiting)
        return <ScaleDialog limitingMmol={rx?.totals?.limiting_mmol ?? null} limitingName={lim?.name || null} onScale={scaleUp} onClose={() => setScaling(false)} />
      })()}
      {writeup && <WriteupDialog experimentId={exp.id} canInsert={editable} onClose={() => setWriteup(false)}
        onInserted={async () => { await load(); toast('Write-up added as a section') }} />}
      {dialog && <SignDialog exp={exp} action={dialog} onClose={() => setDialog(null)} onDone={r => {
        setExp(r); setDialog(null); setVerify(null); setFresh(true); setReloadKey(k => k + 1)
        toast({ sign: 'Signed and locked', witness: 'Witnessed', reject: 'Returned to author', withdraw: 'Signature withdrawn', void: 'Voided' }[dialog])
        refreshNotebooks()
      }} />}
    </article>
  )
}
