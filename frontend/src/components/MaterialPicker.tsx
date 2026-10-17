import { useEffect, useState } from 'react'
import { api, fmt, type Material, type StoichRow } from '../api'
import { Mol, useDebounced } from './bits'
import Modal from './Modal'

export default function MaterialPicker({ onPick, onClose }: {
  onPick: (m: Material, role: StoichRow['role']) => void; onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [role, setRole] = useState<StoichRow['role']>('reagent')
  const [items, setItems] = useState<Material[]>([])
  const dq = useDebounced(q, 200)
  useEffect(() => { api.get<Material[]>(`/api/materials?q=${encodeURIComponent(dq)}&limit=40`).then(setItems) }, [dq])

  const pick = async (m: Material) => onPick(await api.get<Material>(`/api/materials/${m.id}`), role)

  return (
    <Modal title="Add from registry" onClose={onClose} footer={<button className="btn" onClick={onClose}>Close</button>}>
      <div className="row" style={{ marginBottom: 10 }}>
        <input className="input grow" autoFocus placeholder="Name, CAS or formula" value={q} onChange={e => setQ(e.target.value)} />
        <select className="select" value={role} onChange={e => setRole(e.target.value as StoichRow['role'])} aria-label="Add as">
          <option value="reactant">as reactant</option><option value="reagent">as reagent</option>
          <option value="catalyst">as catalyst</option><option value="solvent">as solvent</option>
        </select>
      </div>
      <ul className="list" style={{ maxHeight: 360, overflowY: 'auto' }}>
        {items.map(m => (
          <li key={m.id}>
            <button className="list-row btn ghost" style={{ gridTemplateColumns: '80px 1fr auto', width: '100%', display: 'grid', textAlign: 'left', borderRadius: 0 }} onClick={() => pick(m)}>
              <span className="mat-thumb" style={{ width: 80, height: 48 }}>{m.smiles && <Mol smiles={m.smiles} w={160} h={96} />}</span>
              <span><b style={{ fontWeight: 600 }}>{m.name}</b><span className="faint" style={{ display: 'block' }}>{m.mid} · {m.cas || 'no CAS'} · {m.formula}</span></span>
              <span className="faint">{fmt(m.mw, 2)} g/mol<br />{m.available_lots} lot{m.available_lots === 1 ? '' : 's'}</span>
            </button>
          </li>
        ))}
        {!items.length && <li className="empty">No matching materials. Register new ones on the Materials page.</li>}
      </ul>
    </Modal>
  )
}
