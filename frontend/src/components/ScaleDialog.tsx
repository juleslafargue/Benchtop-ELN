import { useState } from 'react'
import { fmt } from '../api'
import Modal from './Modal'

/** Pick a scale factor directly, or by the new amount of limiting reagent. */
export default function ScaleDialog({ limitingMmol, limitingName, onScale, onClose }: {
  limitingMmol: number | null; limitingName: string | null; onScale: (f: number) => void; onClose: () => void
}) {
  const [factor, setFactor] = useState('2')
  const f = Number(factor)
  const ok = Number.isFinite(f) && f >= 0.001 && f <= 1000
  return (
    <Modal title="Repeat at another scale" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!ok} onClick={() => onScale(f)}>Create ×{ok ? +f.toPrecision(4) : '?'} experiment</button></>}>
      <div className="stack">
        <p style={{ margin: 0 }}>Makes a new open experiment with the same sections. Typed amounts are multiplied, equivalents stay the same,
          solvents set to a target concentration are recalculated, and results (isolated masses, spectra) start empty. The risk assessment is cleared so you reassess at the new scale.</p>
        <div className="chips">{[0.5, 2, 5, 10].map(x => <button key={x} className={`chip${f === x ? ' on' : ''}`} onClick={() => setFactor(String(x))}>×{x}</button>)}</div>
        <div className="form-grid">
          <label className="field">Scale factor<input className="input" inputMode="decimal" value={factor} onChange={e => setFactor(e.target.value)} /></label>
          {limitingMmol ? <label className="field">…or new amount of {limitingName || 'limiting reagent'} (mmol)
            <input className="input" inputMode="decimal" value={ok ? +(limitingMmol * f).toPrecision(4) : ''} onChange={e => { const v = Number(e.target.value); if (v > 0) setFactor(String(+(v / limitingMmol).toPrecision(6))) }} /></label> : null}
        </div>
        {limitingMmol && ok ? <p className="faint" style={{ margin: 0 }}>{fmt(limitingMmol, 4)} mmol → {fmt(limitingMmol * f, 4)} mmol{f >= 5 ? '. At larger scale, think about heat removal, addition rates and quench volumes.' : ''}</p> : null}
      </div>
    </Modal>
  )
}
