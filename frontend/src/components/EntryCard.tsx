import { useCallback, useEffect, useRef, useState } from 'react'
import { KindIcon } from './AddSection'
import { api, type Attachment, type Entry } from '../api'
import TextEntry from './TextEntry'
import ReactionEntry from './ReactionEntry'
import TableEntry from './TableEntry'
import FileEntry from './FileEntry'
import PurificationEntry from './PurificationEntry'
import CharacterizationEntry from './CharacterizationEntry'
import VersionsDialog from './VersionsDialog'
import SafetyEntry from './SafetyEntry'
import ScreenEntry from './ScreenEntry'

type SaveState = 'saved' | 'dirty' | 'saving' | 'error'
const KIND_LABEL = { text: 'Text', reaction: 'Reaction & stoichiometry', table: 'Data table', file: 'Files', purification: 'Work-up & purification', characterization: 'Characterisation', safety: 'Safety & risk assessment', screen: 'Screening grid' }

export interface EntryEditorProps {
  content: any
  onChange: (c: any) => void
  readOnly: boolean
  experimentId: number
  attachments: Attachment[]
  onAttachmentsChanged: () => void
  context: EntryContext
}

/** Experiment-level facts an editor may need, e.g. the product formula for scaling NMR integrals. */
export interface EntryContext { productFormula: string | null; productSmiles: string | null; productMW: number | null; openAttachmentId: number | null }

export default function EntryCard({ entry, readOnly, experimentId, attachments, onAttachmentsChanged, onDeleted, onMove, isFirst, isLast, context, comments, onComment }: {
  entry: Entry; readOnly: boolean; experimentId: number; attachments: Attachment[]; context: EntryContext
  onAttachmentsChanged: () => void; onDeleted: () => void
  onMove: (dir: -1 | 1) => void; isFirst: boolean; isLast: boolean
  comments?: number; onComment?: () => void
}) {
  const [content, setContent] = useState(entry.content)
  const [title, setTitle] = useState(entry.title)
  const [state, setState] = useState<SaveState>('saved')
  const [error, setError] = useState('')
  const [showVersions, setShowVersions] = useState(false)
  const [version, setVersion] = useState(entry.version)
  const versionRef = useRef(entry.version)
  const edits = useRef(0)
  const latest = useRef({ content, title })
  latest.current = { content, title }

  const save = useCallback(async () => {
    const mark = edits.current
    setState('saving')
    try {
      const r = await api.put<Entry>(`/api/entries/${entry.id}`, {
        title: latest.current.title, content: latest.current.content, expected_version: versionRef.current,
      })
      versionRef.current = r.version
      setVersion(r.version)
      if (edits.current === mark) {
        setContent(r.content) // adopt server-normalised content (sanitised HTML, recomputed stoichiometry)
        setState('saved')
        if (entry.kind === 'reaction') window.dispatchEvent(new CustomEvent('benchtop:reaction-saved', { detail: { experimentId: entry.experiment_id } }))
      } else setState('dirty')
      setError('')
    } catch (e: any) {
      setState('error')
      setError(e.message)
    }
  }, [entry.id])

  useEffect(() => {
    if (state !== 'dirty') return
    const t = setTimeout(save, 900)
    return () => clearTimeout(t)
  }, [state, content, title, save])

  useEffect(() => {
    if (state === 'saved') return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [state])

  const change = (c: any) => { edits.current++; setContent(c); setState('dirty') }

  const remove = async () => {
    if (!confirm(`Remove "${title}"? It stays in the version history.`)) return
    await api.del(`/api/entries/${entry.id}`)
    onDeleted()
  }

  const props: EntryEditorProps = { content, onChange: change, readOnly, experimentId, attachments, onAttachmentsChanged, context }
  const stateText = { saved: `v${version}`, dirty: 'Unsaved', saving: 'Saving…', error: 'Not saved' }[state]

  return (
    <section className="entry" id={`entry-${entry.id}`} aria-label={title}>
      <header className="entry-head">
        <input className="entry-title" value={title} disabled={readOnly} aria-label="Section title"
          onChange={e => { edits.current++; setTitle(e.target.value); setState('dirty') }} />
        <span className="entry-kind"><KindIcon kind={entry.kind} />{KIND_LABEL[entry.kind]}</span>
        <span className={`save-state${state === 'dirty' || state === 'saving' ? ' dirty' : ''}${state === 'error' ? ' err' : ''}`} title={error}>{stateText}</span>
        {onComment && <button className="btn ghost sm entry-comments" onClick={onComment} aria-label={comments ? `${comments} open comment${comments === 1 ? '' : 's'} on this section` : 'Comment on this section'}
          title="Comment on this section">
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" /></svg>
          {comments ? <span className="n">{comments}</span> : null}</button>}
        <button className="btn ghost sm" onClick={() => setShowVersions(true)}>History</button>
        {!readOnly && <>
          <button className="btn ghost sm" disabled={isFirst} onClick={() => onMove(-1)} aria-label="Move section up">↑</button>
          <button className="btn ghost sm" disabled={isLast} onClick={() => onMove(1)} aria-label="Move section down">↓</button>
          <button className="btn ghost sm" onClick={remove} aria-label="Remove section">Remove</button>
        </>}
      </header>
      {state === 'error' && <p className="error" style={{ padding: '6px 16px 0' }}>{error} {error.includes('reload') && <button className="btn sm" onClick={() => location.reload()}>Reload</button>}</p>}
      <div className="entry-body">
        {entry.kind === 'text' && <TextEntry {...props} />}
        {entry.kind === 'reaction' && <ReactionEntry {...props} />}
        {entry.kind === 'table' && <TableEntry {...props} />}
        {entry.kind === 'file' && <FileEntry {...props} />}
        {entry.kind === 'purification' && <PurificationEntry {...props} />}
        {entry.kind === 'characterization' && <CharacterizationEntry {...props} />}
        {entry.kind === 'safety' && <SafetyEntry {...props} />}
        {entry.kind === 'screen' && <ScreenEntry {...props} />}
      </div>
      {showVersions && <VersionsDialog entryId={entry.id} kind={entry.kind} onClose={() => setShowVersions(false)} />}
    </section>
  )
}
