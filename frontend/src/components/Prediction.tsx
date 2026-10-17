import { useEffect, useState } from 'react'
import { api } from '../api'
import { useApp } from '../App'

export interface PredGroup {
  atoms: number[]; nH: number; element: string; exchangeable: boolean; aromatic: boolean
  shift: number | null; sd?: number; n?: number; sphere?: number; lo?: number; hi?: number
  confidence: 'high' | 'medium' | 'low' | 'none'; vicinal_h?: number
}
export interface Prediction {
  nucleus: '1H' | '13C'; smiles: string; groups: PredGroup[]; covered: number; total: number
  source: { source?: string; built_at?: string; molecules?: string; assigned_atoms?: string; bundled?: boolean }
}
export interface NmrStatus {
  available: boolean; updating: boolean; progress: string; last_error: string; refresh_days: number
  meta?: Record<string, string>; age_days?: number | null
}
export interface ObservedSignal { lo: number; hi: number; center: number; h: number | null }
export interface Assignment {
  bySignal: { groups: number[]; predH: number; worst: number }[]   // index = observed signal
  byGroup: { signal: number | null; delta: number | null }[]       // index = predicted group
}

export const groupLabel = (g: PredGroup) => g.atoms.map(i => i + 1).join(',')
const MULT = ['s', 'd', 't', 'q', 'p', 'sext', 'sept']
export const predMult = (g: PredGroup) => (g.exchangeable ? 'br s' : g.aromatic ? 'm' : MULT[g.vicinal_h ?? 0] || 'm')
// How far an observed shift may sit from the prediction before we flag it: the 90th-percentile error
// for that confidence level on NMRShiftDB2 molecules the table never saw (or twice the local spread if wider).
const TOL: Record<string, Record<string, number>> = { '13C': { high: 3, medium: 6, low: 14, none: 0 }, '1H': { high: 0.35, medium: 0.65, low: 1.2, none: 0 } }
export const tolerance = (g: PredGroup, nuc: string) => Math.max(TOL[nuc][g.confidence] ?? 0, 2 * (g.sd ?? 0))

/** Match predicted groups to observed signals. Several groups may share one signal (overlapping multiplets). */
export function assign(groups: PredGroup[], signals: ObservedSignal[], nuc: string): Assignment {
  const bySignal = signals.map(() => ({ groups: [] as number[], predH: 0, worst: 0 }))
  const byGroup = groups.map(() => ({ signal: null as number | null, delta: null as number | null }))
  const rank = { high: 0, medium: 1, low: 2, none: 3 }
  const order = groups.map((g, i) => i).filter(i => groups[i].shift !== null).sort((a, b) => rank[groups[a].confidence] - rank[groups[b].confidence])
  const pad = nuc === '13C' ? 0 : 0.05
  const dist = (s: number, sig: ObservedSignal) => (s >= sig.lo - pad && s <= sig.hi + pad ? 0 : Math.min(Math.abs(s - sig.lo), Math.abs(s - sig.hi)))
  // 1H: a signal whose integral is already used up by other groups is a worse home than a slightly
  // farther one with room (two aromatic doublets predicted 0.04 ppm apart go to two signals, not one)
  // 13C: one peak per carbon unless nothing else fits (two carbons can coincide, as ibuprofen's 45.0 pair does)
  const full = nuc === '13C' ? 2 : 0.3
  for (const gi of order) {
    const g = groups[gi], s = g.shift!
    let best = -1, bd = Infinity, bdist = Infinity
    signals.forEach((sig, k) => {
      const d = dist(s, sig)
      const over = (nuc === '13C' ? bySignal[k].groups.length > 0 : sig.h != null && bySignal[k].predH + g.nH > 1.25 * sig.h + 0.25) ? full : 0
      const c = d + over + Math.abs(s - sig.center) * 1e-3
      if (c < bd) { bd = c; best = k; bdist = d }
    })
    if (best < 0 || bdist > tolerance(g, nuc)) continue
    byGroup[gi] = { signal: best, delta: signals[best].center - s }
    bySignal[best].groups.push(gi)
    bySignal[best].predH += g.nH
  }
  // then improve the whole picture: move or swap groups while the total cost (distance plus
  // overfilled signals) goes down. Greedy alone gives 139.5 the 140.9 peak and leaves 137.0 empty.
  const cand = groups.map((g, gi) => (g.shift == null ? [] : signals.map((sig, k) => k).filter(k => dist(g.shift!, signals[k]) <= tolerance(g, nuc))))
  const sigCost = (k: number, members: number[]) => {
    if (!members.length) return 0
    if (nuc === '13C') return full * (members.length - 1)
    const h = signals[k].h, ph = members.reduce((t, gi) => t + groups[gi].nH, 0)
    return h == null ? 0 : full * Math.max(0, ph - 1.25 * h - 0.25)
  }
  const total = () => {
    let c = 0
    const members = signals.map(() => [] as number[])
    groups.forEach((g, gi) => {
      const k = byGroup[gi].signal
      if (g.shift == null) return
      if (k == null) c += tolerance(g, nuc) + full
      else { c += dist(g.shift, signals[k]) + Math.abs(g.shift - signals[k].center) * 1e-3; members[k].push(gi) }
    })
    return c + members.reduce((t, m, k) => t + sigCost(k, m), 0)
  }
  let cur = total()
  for (let iter = 0, improved = true; improved && iter < 20; iter++) {
    improved = false
    for (let gi = 0; gi < groups.length; gi++) {
      for (const k of cand[gi]) {
        const was = byGroup[gi].signal
        if (k === was) continue
        byGroup[gi].signal = k
        const c = total()
        if (c < cur - 1e-9) { cur = c; improved = true } else byGroup[gi].signal = was
      }
      for (let gj = gi + 1; gj < groups.length; gj++) {
        const A = byGroup[gi].signal, B = byGroup[gj].signal
        if (A == null || B == null || A === B || !cand[gi].includes(B) || !cand[gj].includes(A)) continue
        byGroup[gi].signal = B; byGroup[gj].signal = A
        const c = total()
        if (c < cur - 1e-9) { cur = c; improved = true } else { byGroup[gi].signal = A; byGroup[gj].signal = B }
      }
    }
  }
  // keep the order: if a group predicted further downfield landed on a signal further upfield than
  // another group's, and swapping fits both within tolerance, swap them
  for (let pass = 0; pass < 3; pass++) {
    for (let a = 0; a < groups.length; a++) for (let b = 0; b < groups.length; b++) {
      const A = byGroup[a].signal, B = byGroup[b].signal
      if (A == null || B == null || A === B || groups[a].shift! <= groups[b].shift! || signals[A].center >= signals[B].center) continue
      if (groups[a].nH !== groups[b].nH) continue
      if (dist(groups[a].shift!, signals[B]) > tolerance(groups[a], nuc) || dist(groups[b].shift!, signals[A]) > tolerance(groups[b], nuc)) continue
      byGroup[a].signal = B; byGroup[b].signal = A
    }
  }
  bySignal.forEach(x => { x.groups = []; x.predH = 0; x.worst = 0 })
  byGroup.forEach((x, gi) => {
    if (x.signal == null) return
    x.delta = signals[x.signal].center - groups[gi].shift!
    const t = bySignal[x.signal]
    t.groups.push(gi); t.predH += groups[gi].nH; t.worst = Math.max(t.worst, Math.abs(x.delta))
  })
  return { bySignal, byGroup }
}

/** Fetch a prediction for the product; handles "no data yet" and lets admins start the download. */
export function usePrediction(smiles: string | null | undefined, nucleus: '1H' | '13C' | null) {
  const [pred, setPred] = useState<Prediction | null>(null)
  const [status, setStatus] = useState<NmrStatus | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!smiles || !nucleus) { setPred(null); return }
    let alive = true
    api.get<Prediction>(`/api/nmr/predict?smiles=${encodeURIComponent(smiles)}&nucleus=${nucleus}`)
      .then(p => { if (alive) { setPred(p); setErr('') } })
      .catch(async e => {
        if (!alive) return
        setPred(null)
        try { setStatus(await api.get<NmrStatus>('/api/nmr/status')) } catch { /* ignore */ }
        setErr(e.message)
      })
    return () => { alive = false }
  }, [smiles, nucleus, tick])
  // while an update runs, poll and retry when it finishes
  useEffect(() => {
    if (!status?.updating) return
    const t = setInterval(async () => {
      try {
        const s = await api.get<NmrStatus>('/api/nmr/status')
        setStatus(s)
        if (!s.updating) setTick(x => x + 1)
      } catch { /* ignore */ }
    }, 2500)
    return () => clearInterval(t)
  }, [status?.updating])
  const startUpdate = async () => {
    try { setStatus(await api.post<NmrStatus>('/api/nmr/update', {})) } catch (e: any) { setErr(e.message) }
  }
  return { pred, status, err, startUpdate }
}

/** Numbered structure, a list of predicted groups and how they line up with the spectrum. */
export function PredictionPanel({ pred, assignment, nucleus, observedLabel, status, err, onStartUpdate }: {
  pred: Prediction | null; assignment: Assignment | null; nucleus: '1H' | '13C'; observedLabel: (k: number) => string
  status: NmrStatus | null; err: string; onStartUpdate: () => void
}) {
  const { user } = useApp()
  const [hl, setHl] = useState<number[]>([])
  if (!pred) {
    if (!err) return null
    const notReady = /downloaded/.test(err) || (status && !status.available)
    return (
      <div className="pred-panel">
        <h4 className="sp-h4">Predicted shifts</h4>
        {notReady ? <>
          <p className="faint" style={{ margin: 0, fontSize: 13 }}>Prediction uses the open NMRShiftDB2 database. {err} Downloading the current export takes a few minutes; Benchtop then checks for a newer one every {status?.refresh_days ?? 30} days.</p>
          {status?.updating ? <p style={{ margin: 0, fontSize: 13 }}><b>Working:</b> {status.progress}</p>
            : user.role === 'admin' ? <div><button className="btn sm" onClick={onStartUpdate}>Download prediction data</button>
              {status?.last_error && <p className="error" style={{ margin: '6px 0 0', fontSize: 12.5 }}>Last attempt: {status.last_error}</p>}</div>
              : <p className="faint" style={{ margin: 0, fontSize: 13 }}>Ask an administrator to download it from this panel or the Admin page, or run <code>./benchtop update-nmr-data</code>.</p>}
        </> : <p className="faint" style={{ margin: 0, fontSize: 13 }}>{err}</p>}
      </div>
    )
  }
  const tolOk = (gi: number) => assignment?.byGroup[gi].delta !== null && assignment?.byGroup[gi].delta !== undefined
  const matched = pred.groups.filter((g, gi) => g.shift !== null && tolOk(gi)).length
  const predictable = pred.groups.filter(g => g.shift !== null).length
  return (
    <div className="pred-panel">
      <h4 className="sp-h4">Predicted shifts <span className="faint">· {nucleus}</span></h4>
      <div className="pred-grid">
        <img className="pred-struct" alt="Structure with atom numbers" src={`/api/chem/depict?smiles=${encodeURIComponent(pred.smiles)}&w=360&h=240&numbers=true${hl.length ? `&atoms=${hl.join(',')}` : ''}`} />
        <table className="plain pred-table">
          <thead><tr><th>Atoms</th>{nucleus === '1H' && <th>H</th>}<th>Predicted</th><th>Observed</th></tr></thead>
          <tbody>{pred.groups.map((g, gi) => {
            const a = assignment?.byGroup[gi]
            return <tr key={gi} onMouseEnter={() => setHl(g.atoms)} onMouseLeave={() => setHl([])} onFocus={() => setHl(g.atoms)} tabIndex={0}>
              <td>{groupLabel(g)}{g.exchangeable ? <span className="faint"> (OH/NH)</span> : null}</td>
              {nucleus === '1H' && <td>{g.nH}</td>}
              <td title={g.shift !== null ? `${g.n} example${g.n === 1 ? '' : 's'} in NMRShiftDB2 with the same surroundings out to ${g.sphere} bond${g.sphere === 1 ? '' : 's'}${g.lo !== undefined ? `; range ${g.lo}–${g.hi}` : ''}` : 'No matching environment in the database'}>
                {g.shift !== null ? <>{g.shift.toFixed(nucleus === '1H' ? 2 : 1)}{nucleus === '1H' ? ` ${predMult(g)}` : ''} <span className={`conf ${g.confidence}`}>{g.confidence}</span></> : <span className="faint">no data</span>}</td>
              <td>{a && a.signal !== null && a.delta !== null
                ? <>{observedLabel(a.signal)} <span className={Math.abs(a.delta) <= tolerance(g, nucleus) / 2 ? 'faint' : 'warn-text'}>({a.delta >= 0 ? '+' : ''}{a.delta.toFixed(nucleus === '1H' ? 2 : 1)})</span></>
                : g.shift !== null ? <span className="warn-text">no signal near {g.shift.toFixed(nucleus === '1H' ? 1 : 0)}</span> : ''}</td>
            </tr>
          })}</tbody>
        </table>
      </div>
      <p className="provenance">{matched} of {predictable} predicted {nucleus === '1H' ? 'proton' : 'carbon'} groups match a signal{pred.covered < pred.total ? ` · ${pred.total - pred.covered} without data` : ''}.
        Source: NMRShiftDB2{pred.source.molecules ? `, ${Number(pred.source.molecules).toLocaleString()} structures` : ''}{pred.source.assigned_atoms ? `, ${Number(pred.source.assigned_atoms).toLocaleString()} assigned ${nucleus} shifts` : ''}
        {pred.source.built_at ? ` (${pred.source.bundled ? 'table shipped with Benchtop' : 'downloaded'} ${new Date(pred.source.built_at).toLocaleDateString()})` : ''}.
        HOSE-style environment matching; “high” confidence predictions are typically within {nucleus === '1H' ? '0.1' : '0.5'} ppm, “low” ones only roughly. Diastereotopic protons and second-order effects aren't modelled. Hover a row to see its atoms.</p>
    </div>
  )
}
