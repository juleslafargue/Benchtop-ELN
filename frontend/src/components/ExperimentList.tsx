import { Link } from 'react-router-dom'
import { displayName, when, type ExperimentSummary } from '../api'

export function StatusDot({ status }: { status: string }) {
  return <span className={`status ${status}`}>{status}</span>
}

export default function ExperimentList({ items, empty, showNotebook = true }: {
  items: ExperimentSummary[]; empty: string; showNotebook?: boolean
}) {
  if (!items.length) return <p className="empty">{empty}</p>
  return (
    <ul className="list">
      {items.map(e => (
        <li key={e.id}>
          <Link to={`/experiments/${e.id}`} className="list-row" style={{ gridTemplateColumns: '92px 1fr auto' }}>
            <span className="eid">{e.eid}</span>
            <span style={{ minWidth: 0 }}>
              <span style={{ fontWeight: 500 }}>{e.title}</span>
              <span className="faint" style={{ display: 'block' }}>
                {showNotebook && e.notebook_name ? `${e.notebook_name} · ` : ''}{displayName(e.owner)} · updated {when(e.updated_at)}
                {e.match ? ` · matched in ${e.match}` : ''}
              </span>
              {e.snippet && <span className="faint" style={{ display: 'block', fontStyle: 'italic' }}>{e.snippet}</span>}
              {e.tags?.length > 0 && <span className="row" style={{ gap: 4, marginTop: 3 }}>{e.tags.map(t => <span className="tag" key={t}>{t}</span>)}</span>}
            </span>
            <span style={{ textAlign: 'right' }}>
              <StatusDot status={e.status} />
              {e.score !== undefined && <span className="faint" style={{ display: 'block' }}>score {e.score.toFixed(2)}</span>}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  )
}
