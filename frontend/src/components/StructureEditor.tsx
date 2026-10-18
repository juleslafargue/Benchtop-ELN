// Ketcher (EPAM, Apache-2.0) chemical structure editor, running fully in-browser via its
// WebAssembly "standalone" service, so no external structure server is needed.
import './ketcherShim'
import { useEffect, useRef, useState } from 'react'
import { Editor } from 'ketcher-react'
import { StandaloneStructServiceProvider } from 'ketcher-standalone'
import 'ketcher-react/dist/index.css'
import Modal from './Modal'

const provider = new StandaloneStructServiceProvider() as any

export interface StructureResult { smiles: string; molfile: string }

export default function StructureEditor({ initial, mode, onDone, onClose }: {
  initial?: string; mode: 'molecule' | 'reaction'
  onDone: (r: StructureResult) => void; onClose: () => void
}) {
  const ketcherRef = useRef<any>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const host = useRef<HTMLDivElement>(null)
  // Ketcher uses the wheel to pan and zoom its canvas but doesn't stop the browser scrolling too;
  // swallow the page scroll here (Ketcher has already seen the event by the time it bubbles up).
  useEffect(() => {
    const el = host.current
    if (!el) return
    const stop = (e: WheelEvent) => {
      // let Ketcher's own lists (templates, periodic table, menus) scroll; stop everything else reaching the page
      for (let n = e.target as HTMLElement | null; n && n !== el; n = n.parentElement) {
        const oy = getComputedStyle(n).overflowY
        if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight + 1) {
          const atTop = n.scrollTop <= 0, atEnd = n.scrollTop + n.clientHeight >= n.scrollHeight - 1
          if ((e.deltaY < 0 && !atTop) || (e.deltaY > 0 && !atEnd)) return
        }
      }
      e.preventDefault()
    }
    el.addEventListener('wheel', stop, { passive: false })
    return () => el.removeEventListener('wheel', stop)
  }, [])

  const done = async () => {
    const k = ketcherRef.current
    if (!k) return
    setBusy(true)
    try {
      const smiles: string = await k.getSmiles()
      let molfile = ''
      try { molfile = mode === 'reaction' ? await k.getRxn() : await k.getMolfile() } catch { /* empty canvas */ }
      if (mode === 'reaction' && smiles && !smiles.includes('>')) {
        setErr('Draw a reaction arrow between reactants and products, or switch to a single structure.')
        setBusy(false)
        return
      }
      onDone({ smiles, molfile })
    } catch (e: any) {
      setErr(e?.message || String(e))
      setBusy(false)
    }
  }

  return (
    <Modal wide title={mode === 'reaction' ? 'Draw reaction' : 'Draw structure'} onClose={onClose} footer={<>
      {err && <span className="error grow">{err}</span>}
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy || !ready} onClick={done}>Use this {mode === 'reaction' ? 'reaction' : 'structure'}</button>
    </>}>
      <div className="ketcher-host" ref={host}>
        {!ready && <div className="ketcher-loading" role="status">Loading the structure editor… (a few seconds the first time)</div>}
        <Editor
          staticResourcesUrl=""
          structServiceProvider={provider}
          errorHandler={(m: string) => setErr(m)}
          onInit={(k: any) => {
            ketcherRef.current = k
            setReady(true)
            ;(window as any).ketcher = k // handy for scripting and browser automation
            if (initial) k.setMolecule(initial).catch(() => setErr('Could not load the existing structure'))
          }}
        />
      </div>
    </Modal>
  )
}
