import type { EntryEditorProps } from './EntryCard'

interface Col { name: string; unit?: string }

export default function TableEntry({ content, onChange, readOnly }: EntryEditorProps) {
  const cols: Col[] = content.columns || []
  const rows: string[][] = content.rows || []
  const set = (c: Col[], r: string[][]) => onChange({ ...content, columns: c, rows: r })

  const pasteTsv = (e: React.ClipboardEvent, ri: number, ci: number) => {
    const text = e.clipboardData.getData('text')
    if (!text.includes('\t') && !text.includes('\n')) return
    e.preventDefault()
    const grid = text.replace(/\r/g, '').split('\n').filter(Boolean).map(l => l.split('\t'))
    const width = Math.max(cols.length, ci + Math.max(...grid.map(g => g.length)))
    const nc = [...cols]; while (nc.length < width) nc.push({ name: `Column ${nc.length + 1}` })
    const nr = rows.map(r => [...r, ...Array(width - r.length).fill('')])
    grid.forEach((g, i) => {
      while (nr.length <= ri + i) nr.push(Array(width).fill(''))
      g.forEach((v, j) => { nr[ri + i][ci + j] = v })
    })
    set(nc, nr)
  }

  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="grid-wrap">
        <table className="data">
          <thead><tr>
            {cols.map((c, ci) => (
              <th key={ci}><input aria-label={`Column ${ci + 1} name`} disabled={readOnly} value={c.name}
                onChange={e => set(cols.map((x, j) => (j === ci ? { ...x, name: e.target.value } : x)), rows)} /></th>
            ))}
            {!readOnly && <th style={{ width: 34 }}><button className="btn ghost sm" aria-label="Add column"
              onClick={() => set([...cols, { name: `Column ${cols.length + 1}` }], rows.map(r => [...r, '']))}>+</button></th>}
          </tr></thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>
                {cols.map((_, ci) => (
                  <td key={ci}><input aria-label={`Row ${ri + 1} ${cols[ci].name}`} disabled={readOnly} value={r[ci] ?? ''}
                    onPaste={e => pasteTsv(e, ri, ci)}
                    onChange={e => set(cols, rows.map((x, j) => (j === ri ? cols.map((_, k) => (k === ci ? e.target.value : x[k] ?? '')) : x)))} /></td>
                ))}
                {!readOnly && <td><button className="btn ghost sm" aria-label={`Remove row ${ri + 1}`} onClick={() => set(cols, rows.filter((_, j) => j !== ri))}>×</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!readOnly && <div className="row">
        <button className="btn sm" onClick={() => set(cols, [...rows, cols.map(() => '')])}>Add row</button>
        {cols.length > 1 && <button className="btn ghost sm" onClick={() => set(cols.slice(0, -1), rows.map(r => r.slice(0, cols.length - 1)))}>Remove last column</button>}
        <span className="faint">Paste cells from Excel or Numbers to fill the table.</span>
      </div>}
    </div>
  )
}
