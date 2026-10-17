import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, fmt, type ExperimentSummary, type Material } from '../api'
import ExperimentList from '../components/ExperimentList'
import { Mol, StructureEditor } from '../components/bits'

type Mode = 'text' | 'substructure' | 'similarity'

export default function Search() {
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') || '')
  const [mode, setMode] = useState<Mode>((params.get('mode') as Mode) || 'text')
  const [threshold, setThreshold] = useState(0.5)
  const [res, setRes] = useState<{ experiments: ExperimentSummary[]; materials: Material[] } | null>(null)
  const [err, setErr] = useState('')
  const [drawing, setDrawing] = useState(false)

  const run = async (query = q, m = mode) => {
    if (!query.trim()) return
    setErr('')
    setParams({ q: query, mode: m })
    try { setRes(await api.get(`/api/search?q=${encodeURIComponent(query)}&mode=${m}&threshold=${threshold}`)) }
    catch (e: any) { setErr(e.message); setRes(null) }
  }
  useEffect(() => { if (params.get('q')) run(params.get('q')!, (params.get('mode') as Mode) || 'text') }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const structural = mode !== 'text'
  return (
    <div className="stack" style={{ gap: 20 }}>
      <div><h1 className="page-title">Search</h1><p className="lede">Find experiments by their words or by the structures in their reaction tables.</p></div>
      <form className="panel panel-pad stack" onSubmit={e => { e.preventDefault(); run() }}>
        <div className="row" role="radiogroup" aria-label="Search type">
          {(['text', 'substructure', 'similarity'] as Mode[]).map(m => (
            <button type="button" key={m} role="radio" aria-checked={mode === m} className={`btn sm${mode === m ? ' primary' : ''}`} onClick={() => setMode(m)}>
              {{ text: 'Text', substructure: 'Substructure', similarity: 'Similarity' }[m]}</button>
          ))}
        </div>
        <div className="row" style={{ flexWrap: 'nowrap' }}>
          <input className="input grow" value={q} onChange={e => setQ(e.target.value)} autoFocus
            placeholder={structural ? 'SMILES or SMARTS, e.g. c1ccccc1Br' : 'Words, reagent names, observations…'} aria-label="Search query" />
          {structural && <button type="button" className="btn" onClick={() => setDrawing(true)}>Draw</button>}
          <button className="btn primary">Search</button>
        </div>
        {mode === 'similarity' && <label className="row faint">Tanimoto at least
          <input type="range" min={0.2} max={1} step={0.05} value={threshold} onChange={e => setThreshold(Number(e.target.value))} /> {threshold.toFixed(2)}</label>}
        {structural && q && <div className="mat-thumb" style={{ width: 180, height: 110 }}><Mol smiles={q} w={360} h={220} /></div>}
        {err && <p className="error">{err}</p>}
      </form>
      {res && <>
        <section className="stack" style={{ gap: 8 }}>
          <h2 style={{ fontSize: 17 }}>Experiments ({res.experiments.length})</h2>
          <div className="panel"><ExperimentList items={res.experiments} empty="No experiments match." /></div>
        </section>
        <section className="stack" style={{ gap: 8 }}>
          <h2 style={{ fontSize: 17 }}>Materials ({res.materials.length})</h2>
          <div className="panel"><ul className="list">
            {res.materials.map(m => <li key={m.id}><Link to={`/materials/${m.id}`} className="list-row mat-row">
              <span className="mat-thumb">{m.smiles && <Mol smiles={m.smiles} w={192} h={116} />}</span>
              <span><b style={{ fontWeight: 600 }}>{m.name}</b><span className="faint" style={{ display: 'block' }}>{m.mid}</span></span>
              <span className="hide-sm muted">{m.formula}</span><span className="hide-sm muted">{m.cas}</span>
              <span className="muted">{fmt(m.mw, 2)}</span><span className="faint">{m.score !== undefined ? `score ${m.score.toFixed(2)}` : ''}</span>
            </Link></li>)}
            {!res.materials.length && <li className="empty">No materials match.</li>}
          </ul></div>
        </section>
      </>}
      {drawing && <StructureEditor mode="molecule" initial={q} onClose={() => setDrawing(false)} onDone={r => { setDrawing(false); setQ(r.smiles); run(r.smiles) }} />}
    </div>
  )
}
