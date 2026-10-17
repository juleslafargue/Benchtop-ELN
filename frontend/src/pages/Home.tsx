import { useEffect, useState } from 'react'
import { useApp } from '../App'
import { api, type ExperimentSummary } from '../api'
import ExperimentList from '../components/ExperimentList'
import NewExperimentDialog from '../components/NewExperimentDialog'

export default function Home() {
  const { user, canWrite } = useApp()
  const [mine, setMine] = useState<ExperimentSummary[]>([])
  const [toWitness, setToWitness] = useState<ExperimentSummary[]>([])
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    api.get<ExperimentSummary[]>(`/api/experiments?owner_id=${user.id}`).then(setMine)
    api.get<ExperimentSummary[]>('/api/experiments?status=signed').then(xs => setToWitness(xs.filter(x => x.owner.id !== user.id)))
  }, [user.id])

  const open = mine.filter(e => e.status === 'open')
  const waiting = mine.filter(e => e.status === 'signed')
  const done = mine.filter(e => e.status === 'witnessed' || e.status === 'voided')

  return (
    <div className="stack" style={{ gap: 28 }}>
      <div className="spread">
        <div>
          <h1 className="page-title">Good to see you, {user.full_name.split(' ')[0] || user.username}</h1>
          <p className="lede">{open.length} open {open.length === 1 ? 'experiment' : 'experiments'}, {toWitness.length} waiting for your witness signature.</p>
        </div>
        {canWrite && <button className="btn primary" onClick={() => setCreating(true)}>New experiment</button>}
      </div>

      {toWitness.length > 0 && (
        <section className="stack" style={{ gap: 8 }}>
          <h2 style={{ fontSize: 17 }}>Waiting for you to witness</h2>
          <div className="panel"><ExperimentList items={toWitness} empty="" /></div>
        </section>
      )}
      <section className="stack" style={{ gap: 8 }}>
        <h2 style={{ fontSize: 17 }}>Open</h2>
        <div className="panel"><ExperimentList items={open} empty="Nothing open. Start a new experiment when you're at the bench." /></div>
      </section>
      {waiting.length > 0 && (
        <section className="stack" style={{ gap: 8 }}>
          <h2 style={{ fontSize: 17 }}>Signed, awaiting witness</h2>
          <div className="panel"><ExperimentList items={waiting} empty="" /></div>
        </section>
      )}
      <section className="stack" style={{ gap: 8 }}>
        <h2 style={{ fontSize: 17 }}>Completed</h2>
        <div className="panel"><ExperimentList items={done} empty="Witnessed experiments will appear here." /></div>
      </section>
      {creating && <NewExperimentDialog onClose={() => setCreating(false)} />}
    </div>
  )
}
