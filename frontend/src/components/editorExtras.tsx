// Editor additions: pictures stored as attachments, and EXP-/MAT- numbers that become links with a preview.
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { Extension, Node, mergeAttributes } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import { api, attachmentUrl, type Attachment } from '../api'
import { Mol } from './bits'

/** A picture that lives in the experiment's files: <img data-attachment-id="12">. */
export const AttachmentImage = Node.create({
  name: 'attachmentImage',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,
  addAttributes() {
    return {
      id: { default: null, parseHTML: el => Number(el.getAttribute('data-attachment-id')) || null, renderHTML: a => ({ 'data-attachment-id': a.id }) },
      alt: { default: '', parseHTML: el => el.getAttribute('alt') || '', renderHTML: a => (a.alt ? { alt: a.alt } : {}) },
    }
  },
  parseHTML() { return [{ tag: 'img[data-attachment-id]' }] },
  renderHTML({ HTMLAttributes }) { return ['img', mergeAttributes(HTMLAttributes)] },
  addNodeView() {
    return ({ node }) => {
      const wrap = document.createElement('figure')
      wrap.className = 'att-img'
      const img = document.createElement('img')
      img.alt = node.attrs.alt || 'picture'
      wrap.appendChild(img)
      if (node.attrs.id) attachmentUrl(node.attrs.id).then(u => { img.src = u }).catch(() => { wrap.classList.add('missing'); img.alt = `${img.alt} (couldn't load)` })
      return { dom: wrap }
    }
  },
})

/** Upload pictures pasted or dropped into a text section, then put them where the cursor is. */
export async function uploadPictures(view: any, files: File[], experimentId: number, onUploaded: () => void, onError: (m: string) => void, pos?: number) {
  {
    for (const f of files) {
      try {
        const fd = new FormData()
        const name = f.name && f.name !== 'image.png' ? f.name : `pasted_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.${(f.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}`
        fd.append('file', f, name)
        const att = await api.post<Attachment>(`/api/experiments/${experimentId}/attachments`, fd)
        const node = view.state.schema.nodes.attachmentImage.create({ id: att.id, alt: att.filename })
        const tr = pos != null ? view.state.tr.insert(Math.min(pos, view.state.doc.content.size), node) : view.state.tr.replaceSelectionWith(node)
        view.dispatch(tr)
        onUploaded()
      } catch (e: any) { onError(e.message) }
    }
  }
}

export function PastePictures(experimentId: number, onUploaded: () => void, onError: (m: string) => void) {
  const upload = (view: any, files: File[], pos?: number) => uploadPictures(view, files, experimentId, onUploaded, onError, pos)
  const images = (list: FileList | null | undefined) => Array.from(list || []).filter(f => f.type.startsWith('image/'))
  return Extension.create({
    name: 'pastePictures',
    addProseMirrorPlugins() {
      return [new Plugin({
        key: new PluginKey('pastePictures'),
        props: {
          handlePaste: (view, event) => {
            const files = images(event.clipboardData?.files)
            if (!files.length || !view.editable) return false
            upload(view, files); return true
          },
          handleDrop: (view, event) => {
            const files = images((event as DragEvent).dataTransfer?.files)
            if (!files.length || !view.editable) return false
            event.preventDefault()
            const at = view.posAtCoords({ left: (event as DragEvent).clientX, top: (event as DragEvent).clientY })
            upload(view, files, at?.pos); return true
          },
        },
      })]
    },
  })
}

const REF = /\b(EXP|MAT)-\d{5}\b/g
/** Highlights EXP-00025 / MAT-00007 wherever they're typed; the hover card does the rest. */
export const RecordLinks = Extension.create({
  name: 'recordLinks',
  addProseMirrorPlugins() {
    const deco = (doc: any) => {
      const out: Decoration[] = []
      doc.descendants((n: any, pos: number) => {
        if (!n.isText || !n.text) return
        for (const m of n.text.matchAll(REF)) out.push(Decoration.inline(pos + m.index!, pos + m.index! + m[0].length, { class: 'ref-link', 'data-ref': m[0] }))
      })
      return DecorationSet.create(doc, out)
    }
    return [new Plugin({
      key: new PluginKey('recordLinks'),
      state: { init: (_, { doc }) => deco(doc), apply: (tr, old) => (tr.docChanged ? deco(tr.doc) : old) },
      props: { decorations(state) { return this.getState(state) } },
    })]
  },
})

interface RefInfo { kind: 'experiment' | 'material'; id: number; code: string; title: string; status?: string; author?: string; date?: string; smiles?: string; formula?: string; mw?: number; cas?: string }
const cache = new Map<string, Promise<RefInfo | null>>()
const lookup = (code: string) => { let p = cache.get(code); if (!p) { p = api.get<RefInfo>(`/api/lookup/${code}`).catch(() => null); cache.set(code, p) } return p }

/** Hover (or tap) an EXP-/MAT- number inside `root` for a preview; click "Open", or ⌘-click the number. */
export function RefPreview({ root }: { root: React.RefObject<HTMLElement> }) {
  const nav = useNavigate()
  const [at, setAt] = useState<{ code: string; x: number; y: number } | null>(null)
  const [info, setInfo] = useState<RefInfo | null | undefined>(undefined)
  const hide = useRef<number>()
  useEffect(() => {
    const el = root.current
    if (!el) return
    const over = (e: MouseEvent) => {
      const t = (e.target as HTMLElement).closest('.ref-link') as HTMLElement | null
      if (!t) return
      clearTimeout(hide.current)
      const r = t.getBoundingClientRect()
      setAt(a => (a?.code === t.dataset.ref ? a : { code: t.dataset.ref!, x: r.left, y: r.bottom }))
    }
    const out = (e: MouseEvent) => { if ((e.target as HTMLElement).closest('.ref-link')) hide.current = window.setTimeout(() => setAt(null), 250) }
    const click = (e: MouseEvent) => {
      const t = (e.target as HTMLElement).closest('.ref-link') as HTMLElement | null
      if (t && (e.metaKey || e.ctrlKey || !el.querySelector('[contenteditable=true]'))) {
        e.preventDefault()
        lookup(t.dataset.ref!).then(i => i && nav(i.kind === 'experiment' ? `/experiments/${i.id}` : `/materials/${i.id}`))
      }
    }
    el.addEventListener('mouseover', over); el.addEventListener('mouseout', out); el.addEventListener('click', click)
    return () => { el.removeEventListener('mouseover', over); el.removeEventListener('mouseout', out); el.removeEventListener('click', click) }
  }, [root, nav])
  useEffect(() => { if (at) { setInfo(undefined); lookup(at.code).then(setInfo) } }, [at?.code]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!at) return null
  const left = Math.max(8, Math.min(at.x, window.innerWidth - 300)), top = at.y + 6 + 220 > window.innerHeight ? at.y - 230 : at.y + 6
  return createPortal(
    <div className="ref-card" style={{ left, top }} onMouseEnter={() => clearTimeout(hide.current)} onMouseLeave={() => setAt(null)} role="tooltip">
      {info === undefined ? <span className="faint">Looking up {at.code}…</span>
        : info === null ? <span className="faint">{at.code} doesn't exist or isn't shared with you.</span>
          : <>
            <div className="faint" style={{ fontSize: 12 }}>{info.code}{info.status ? <> · <span className={`status ${info.status}`}>{info.status === 'open' ? 'draft' : info.status}</span></> : ''}</div>
            <b className="ref-title">{info.title}</b>
            {info.smiles && <span className="ref-mol"><Mol smiles={info.smiles} rxn={info.kind === 'experiment' && info.smiles.includes('>')} w={540} h={150} /></span>}
            <div className="faint" style={{ fontSize: 12 }}>{info.kind === 'experiment' ? `${info.author || ''}${info.date ? ` · ${new Date(info.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}` : ''}`
              : [info.formula, info.mw ? `${info.mw.toFixed(2)} g/mol` : '', info.cas].filter(Boolean).join(' · ')}</div>
            <button className="btn sm" onClick={() => nav(info.kind === 'experiment' ? `/experiments/${info.id}` : `/materials/${info.id}`)}>Open {info.code}</button>
          </>}
    </div>, document.body)
}
