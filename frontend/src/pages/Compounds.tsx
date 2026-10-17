import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt } from '../api'
import { Mol } from '../components/bits'

interface Compound {
  name: string; smiles: string; formula: string; mw: number; logp: number; tpsa: number; hbd: number; hba: number
  rotatable_bonds: number; heavy_atoms: number; aromatic_rings: number; fsp3: number; qed: number | null
  lipinski_violations: string[]; veber_violations: string[]; experiments: { id: number; eid: string }[]
  best_yield: number | null; material: { id: number; mid: string } | null
}
type Key = 'name' | 'mw' | 'logp' | 'tpsa' | 'hbd' | 'hba' | 'rotatable_bonds' | 'fsp3' | 'qed' | 'best_yield'
const COLS: [Key, string, string][] = [
  ['mw', 'MW', 'Molecular weight of the largest fragment'], ['logp', 'cLogP', 'Crippen calculated logP'], ['tpsa', 'TPSA', 'Topological polar surface area (Å²)'],
  ['hbd', 'HBD', 'H-bond donors'], ['hba', 'HBA', 'H-bond acceptors'], ['rotatable_bonds', 'RotB', 'Rotatable bonds'],
  ['fsp3', 'Fsp³', 'Fraction of sp³ carbons'], ['qed', 'QED', 'Quantitative estimate of drug-likeness (0–1, higher is more drug-like)'],
  ['best_yield', 'Best yield', 'Best isolated yield across experiments'],
]

export default function Compounds() {
  const [rows, setRows] = useState<Compound[] | null>(null)
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: 'name', dir: 1 })
  const [onlyRo5, setOnlyRo5] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { api.get<Compound[]>('/api/compounds').then(setRows).catch(e => setErr(e.message)) }, [])
  const shown = useMemo(() => {
    const r = (rows || []).filter(c => !onlyRo5 || (c.lipinski_violations.length <= 1 && !c.veber_violations.length))
    return [...r].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key]
      if (typeof x === 'string' || typeof y === 'string') return String(x).localeCompare(String(y)) * sort.dir
      return ((x ?? -Infinity) as number) > ((y ?? -Infinity) as number) ? sort.dir : -sort.dir
    })
  }, [rows, sort, onlyRo5])
  const th = (key: Key, label: string, title?: string) => (
    <th aria-sort={sort.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'} title={title}>
      <button className="btn ghost sm" style={{ padding: 0, fontWeight: 600 }} onClick={() => setSort(s => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : (key === 'name' ? 1 : -1) }))}>
        {label}{sort.key === key ? (sort.dir === 1 ? ' ↑' : ' ↓') : ''}</button></th>)

  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="spread">
        <div><h1 className="page-title">Compounds</h1>
          <p className="lede">Every product you've made and every registered sample, with medicinal-chemistry descriptors. Calculated with RDKit on the parent structure (salts removed).</p></div>
        <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={onlyRo5} onChange={e => setOnlyRo5(e.target.checked)} /> Drug-like only (≤1 Lipinski violation, passes Veber)</label>
      </div>
      {err && <p className="error">{err}</p>}
      <div className="panel grid-wrap" style={{ border: 0 }}>
        <table className="plain">
          <thead><tr>{th('name', 'Compound')}<th>Structure</th>{COLS.map(([k, l, t]) => th(k, l, t))}<th>Rules</th><th>Made in</th></tr></thead>
          <tbody>
            {shown.map(c => <tr key={c.smiles}>
              <td><b style={{ fontWeight: 600 }}>{c.name || c.formula}</b><div className="faint">{c.formula}{c.material ? <> · <Link to={`/materials/${c.material.id}`}>{c.material.mid}</Link></> : null}</div></td>
              <td><span className="mat-thumb" style={{ width: 110, height: 66, display: 'block' }}><Mol smiles={c.smiles} w={220} h={132} /></span></td>
              <td className="num">{fmt(c.mw, 1)}</td><td className="num">{fmt(c.logp, 2)}</td><td className="num">{fmt(c.tpsa, 1)}</td>
              <td className="num">{c.hbd}</td><td className="num">{c.hba}</td><td className="num">{c.rotatable_bonds}</td>
              <td className="num">{fmt(c.fsp3, 2)}</td><td className="num">{c.qed ?? '—'}</td>
              <td className="num">{c.best_yield !== null ? `${fmt(c.best_yield, 1)}%` : '—'}</td>
              <td style={{ fontSize: 12.5 }}>{!c.lipinski_violations.length && !c.veber_violations.length ? <span className="faint">Passes Lipinski & Veber</span>
                : [...c.lipinski_violations, ...c.veber_violations].map(v => <div key={v}>{v}</div>)}</td>
              <td style={{ fontSize: 13 }}>{c.experiments.map(e => <div key={e.id}><Link to={`/experiments/${e.id}`}>{e.eid}</Link></div>)}</td>
            </tr>)}
            {rows && !shown.length && <tr><td colSpan={13} className="empty">No compounds yet. Products from your reaction tables appear here.</td></tr>}
            {!rows && !err && <tr><td colSpan={13} className="empty">Calculating…</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
