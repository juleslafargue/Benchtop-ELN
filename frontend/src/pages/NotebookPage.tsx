import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useApp } from '../App'
import { api, displayName, type ExperimentSummary, type Status } from '../api'
import ExperimentList from '../components/ExperimentList'
import NewExperimentDialog from '../components/NewExperimentDialog'

export default function NotebookPage() {
  const { id } = useParams()
  const { notebooks, canWrite } = useApp()
  const nb = notebooks.find(n => n.id === Number(id))
  const [items, setItems] = useState<ExperimentSummary[]>([])
  const [filter, setFilter] = useState<Status | ''>('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    api.get<ExperimentSummary[]>(`/api/experiments?notebook_id=${id}${filter ? `&status=${filter}` : ''}`).then(setItems)
  }, [id, filter])

  if (!nb) return <p className="empty">Notebook not found.</p>
  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="spread">
        <div>
          <h1 className="page-title">{nb.name}</h1>
          <p className="lede">{nb.description || 'No description.'} <span className="faint">Owned by {displayName(nb.owner)}.</span></p>
        </div>
        {canWrite && <button className="btn primary" onClick={() => setCreating(true)}>New experiment</button>}
      </div>
      <div className="row" role="group" aria-label="Filter by status">
        {(['', 'open', 'signed', 'witnessed', 'voided'] as const).map(s => (
          <button key={s} className={`btn sm${filter === s ? ' primary' : ''}`} onClick={() => setFilter(s)}>
            {s ? `${s[0].toUpperCase()}${s.slice(1)} (${nb.status_counts[s] ?? 0})` : `All (${nb.experiment_count})`}
          </button>
        ))}
      </div>
      <div className="panel"><ExperimentList items={items} showNotebook={false} empty="No experiments here yet." /></div>
      {creating && <NewExperimentDialog notebookId={nb.id} onClose={() => setCreating(false)} />}
    </div>
  )
}
