import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../App'
import { api, type ExperimentSummary } from '../api'
import ExperimentList from '../components/ExperimentList'

export default function Templates() {
  const { canWrite } = useApp()
  const nav = useNavigate()
  const [items, setItems] = useState<ExperimentSummary[]>([])
  useEffect(() => { api.get<ExperimentSummary[]>('/api/experiments?templates=true').then(setItems) }, [])
  const create = async () => {
    const t = await api.post<ExperimentSummary>('/api/experiments', { title: 'New template', is_template: true })
    nav(`/experiments/${t.id}`)
  }
  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="spread">
        <div><h1 className="page-title">Templates</h1><p className="lede">Reusable experiment layouts. New experiments copy a template's sections and starting content.</p></div>
        {canWrite && <button className="btn primary" onClick={create}>New template</button>}
      </div>
      <div className="panel"><ExperimentList items={items} showNotebook={false} empty="No templates yet. Save any experiment as a template from its page." /></div>
    </div>
  )
}
