import { useEffect, useRef, useState, type ReactNode } from 'react'
import { EditorContent, useEditor, type Editor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Subscript from '@tiptap/extension-subscript'
import Superscript from '@tiptap/extension-superscript'
import Underline from '@tiptap/extension-underline'
import Highlight from '@tiptap/extension-highlight'
import TextAlign from '@tiptap/extension-text-align'
import TextStyle from '@tiptap/extension-text-style'
import Color from '@tiptap/extension-color'
import Link from '@tiptap/extension-link'
import TaskList from '@tiptap/extension-task-list'
import TaskItem from '@tiptap/extension-task-item'
import Table from '@tiptap/extension-table'
import TableRow from '@tiptap/extension-table-row'
import TableCell from '@tiptap/extension-table-cell'
import TableHeader from '@tiptap/extension-table-header'
import Placeholder from './placeholder'
import type { EntryEditorProps } from './EntryCard'

// Colours that stay readable on paper and in dark mode; text keeps the theme's ink by default.
const COLOURS: [string, string | null][] = [['Default', null], ['Red', '#c0392b'], ['Orange', '#c46a12'], ['Green', '#2e8540'], ['Blue', '#2f6fb5'], ['Purple', '#7d4cad']]
const SYMBOLS: [string, string[]][] = [
  ['Common', ['°C', '±', '×', '÷', '≈', '≠', '≤', '≥', '~', '‰', '½', '¼', '·', '…', '§', '‡']],
  ['Arrows', ['→', '←', '⇌', '⇄', '↑', '↓', '⟶', '⇒', '↻']],
  ['Greek', ['α', 'β', 'γ', 'δ', 'ε', 'ζ', 'η', 'θ', 'κ', 'λ', 'µ', 'ν', 'π', 'ρ', 'σ', 'τ', 'φ', 'χ', 'ψ', 'ω', 'Δ', 'Σ', 'Φ', 'Ω']],
  ['Units', ['µL', 'µmol', 'mmol', 'mg', 'mL', 'M', 'equiv', 'h', 'min', 'rt', 'Å', 'ppm', 'Hz', 'nm', 'cm⁻¹', 'm/z']],
]

/** Subscript the numbers in chemical formulas within the selection (or the word at the caret):
 *  CH2Cl2 → CH₂Cl₂, Ca(OH)2 → Ca(OH)₂. Leaves numbers that don't follow an element or bracket alone. */
function formatFormula(editor: Editor) {
  const { state } = editor
  let { from, to } = state.selection
  if (from === to) { // expand to the word under the caret
    const $p = state.selection.$from, text = $p.parent.textContent, off = $p.parentOffset
    let a = off, b = off
    while (a > 0 && /\S/.test(text[a - 1])) a--
    while (b < text.length && /\S/.test(text[b])) b++
    from = $p.start() + a; to = $p.start() + b
  }
  const ranges: [number, number][] = []
  state.doc.nodesBetween(from, to, (node, pos) => {
    if (!node.isText || !node.text) return
    const re = /(?<=[A-Za-z)\]])\d+/g
    let m: RegExpExecArray | null
    while ((m = re.exec(node.text))) {
      const s = pos + m.index, e = s + m[0].length
      if (e > from && s < to) ranges.push([Math.max(s, from), Math.min(e, to)])
    }
  })
  if (!ranges.length) return
  const sub = state.schema.marks.subscript
  let tr = state.tr
  for (const [s, e] of ranges) tr = tr.addMark(s, e, sub.create())
  editor.view.dispatch(tr)
  editor.commands.focus()
}

function stamp() {
  return new Date().toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** A toolbar dropdown that closes on outside click or Escape. */
function Menu({ label, title, children, wide, on }: { label: ReactNode; title: string; children: (close: () => void) => ReactNode; wide?: boolean; on?: boolean }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const out = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', out); document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', out); document.removeEventListener('keydown', esc) }
  }, [open])
  return (
    <div className="rt-menu" ref={ref}>
      <button type="button" className={`rt-drop${on ? ' on' : ''}`} title={title} aria-label={title} aria-haspopup="true" aria-expanded={open}
        onMouseDown={e => e.preventDefault()} onClick={() => setOpen(o => !o)}>{label}<span className="rt-caret" aria-hidden>▾</span></button>
      {open && <div className={`rt-pop${wide ? ' wide' : ''}`} role="menu">{children(() => setOpen(false))}</div>}
    </div>
  )
}

const Icon = ({ d, fill }: { d: string; fill?: boolean }) => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill={fill ? 'currentColor' : 'none'} stroke={fill ? 'none' : 'currentColor'} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
)
const ICONS = {
  undo: 'M5.5 4L2.5 7l3 3M2.5 7h7a4 4 0 010 8h-2',
  redo: 'M10.5 4l3 3-3 3M13.5 7h-7a4 4 0 000 8h2',
  bullet: 'M6 4h8M6 8h8M6 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01',
  check: 'M2 4.5l1.3 1.3L5.5 3.5M2 10.5l1.3 1.3 2.2-2.3M8 4.5h6M8 10.5h6',
  link: 'M6.5 9.5a3 3 0 004.2 0l2.1-2.1a3 3 0 00-4.2-4.2l-.7.7M9.5 6.5a3 3 0 00-4.2 0L3.2 8.6a3 3 0 004.2 4.2l.7-.7',
  quote: 'M3 4h10M3 4v8M6 7h7M6 10h5',
  rule: 'M2 8h12',
  clock: 'M8 2.5a5.5 5.5 0 110 11 5.5 5.5 0 010-11zM8 5v3l2 1.5',
  clear: 'M3 3l10 10M6 3h7M9.5 3L8 7.5M4 13h5',
  alignL: 'M2 4h12M2 7h8M2 10h12M2 13h8',
  alignC: 'M2 4h12M4 7h8M2 10h12M4 13h8',
  alignR: 'M2 4h12M6 7h8M2 10h12M6 13h8',
}

export default function TextEntry({ content, onChange, readOnly }: EntryEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit, Subscript, Superscript, Underline, Highlight, TextStyle, Color,
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
      Link.configure({ openOnClick: false, autolink: true, HTMLAttributes: { rel: 'noopener noreferrer', target: '_blank' } }),
      TaskList, TaskItem.configure({ nested: true }),
      Table.configure({ resizable: false }), TableRow, TableCell, TableHeader,
      Placeholder('Write here. Ctrl+, for subscript, Ctrl+. for superscript, “[ ] ” for a checklist.'),
    ],
    content: content.html || '',
    editable: !readOnly,
    onUpdate: ({ editor }) => onChange({ ...content, html: editor.getHTML() }),
  }, [readOnly])

  // pick up server-normalised HTML without clobbering the caret during typing
  useEffect(() => {
    if (editor && !editor.isFocused && content.html !== editor.getHTML()) editor.commands.setContent(content.html || '', false)
  }, [content.html, editor])

  if (!editor) return null
  const c = () => editor.chain().focus()
  const B = ({ cmd, on, label, title, disabled }: { cmd: () => void; on?: boolean; label: ReactNode; title: string; disabled?: boolean }) => (
    <button type="button" className={on ? 'on' : ''} title={title} aria-label={title} aria-pressed={on === undefined ? undefined : !!on} disabled={disabled}
      onMouseDown={e => e.preventDefault()} onClick={cmd}>{label}</button>
  )
  const Sep = () => <span className="rt-sep" aria-hidden />
  const style = editor.isActive('heading', { level: 2 }) ? 'Heading' : editor.isActive('heading', { level: 3 }) ? 'Subheading' : editor.isActive('codeBlock') ? 'Code' : 'Text'
  const inTable = editor.isActive('table')
  const colour = editor.getAttributes('textStyle').color as string | undefined
  const align = (['center', 'right'] as const).find(a => editor.isActive({ textAlign: a })) || 'left'

  return (
    <div>
      {!readOnly && (
        <div className="rt-toolbar" role="toolbar" aria-label="Formatting">
          <B label={<Icon d={ICONS.undo} />} title="Undo (⌘Z)" cmd={() => c().undo().run()} disabled={!editor.can().undo()} />
          <B label={<Icon d={ICONS.redo} />} title="Redo (⌘⇧Z)" cmd={() => c().redo().run()} disabled={!editor.can().redo()} />
          <Sep />
          <Menu label={<span className="rt-style">{style}</span>} title="Paragraph style">
            {close => <>
              {([['Text', () => c().setParagraph().run(), 'p'], ['Heading', () => c().setHeading({ level: 2 }).run(), 'h2'], ['Subheading', () => c().setHeading({ level: 3 }).run(), 'h3'], ['Code', () => c().setCodeBlock().run(), 'code']] as const)
                .map(([l, fn, cls]) => <button key={l} role="menuitem" className={`rt-item st-${cls}${style === l ? ' on' : ''}`} onMouseDown={e => e.preventDefault()} onClick={() => { fn(); close() }}>{l}</button>)}
            </>}
          </Menu>
          <Sep />
          <B label={<b>B</b>} title="Bold (⌘B)" on={editor.isActive('bold')} cmd={() => c().toggleBold().run()} />
          <B label={<i>I</i>} title="Italic (⌘I)" on={editor.isActive('italic')} cmd={() => c().toggleItalic().run()} />
          <B label={<u>U</u>} title="Underline (⌘U)" on={editor.isActive('underline')} cmd={() => c().toggleUnderline().run()} />
          <B label={<s>S</s>} title="Strikethrough (⌘⇧S)" on={editor.isActive('strike')} cmd={() => c().toggleStrike().run()} />
          <B label="x₂" title="Subscript (⌘,)" on={editor.isActive('subscript')} cmd={() => c().toggleSubscript().run()} />
          <B label="x²" title="Superscript (⌘.)" on={editor.isActive('superscript')} cmd={() => c().toggleSuperscript().run()} />
          <B label={<span className="rt-formula">H₂O</span>} title="Format as chemical formula: subscripts the numbers in the selection or word (CH2Cl2 → CH₂Cl₂)" cmd={() => formatFormula(editor)} />
          <B label={<span className="rt-hl">ab</span>} title="Highlight (⌘⇧H)" on={editor.isActive('highlight')} cmd={() => c().toggleHighlight().run()} />
          <Menu title="Text colour" on={!!colour} label={<span className="rt-colour" style={{ borderBottomColor: colour || 'currentColor' }}>A</span>}>
            {close => <div className="rt-swatches">
              {COLOURS.map(([n, v]) => <button key={n} role="menuitem" title={n} aria-label={n} className={`rt-swatch${(colour || null) === v ? ' on' : ''}`}
                style={{ color: v || 'var(--ink)' }} onMouseDown={e => e.preventDefault()}
                onClick={() => { v ? c().setColor(v).run() : c().unsetColor().run(); close() }}>A</button>)}
            </div>}
          </Menu>
          <Sep />
          <B label={<Icon d={ICONS.bullet} />} title="Bulleted list" on={editor.isActive('bulletList')} cmd={() => c().toggleBulletList().run()} />
          <B label="1." title="Numbered steps" on={editor.isActive('orderedList')} cmd={() => c().toggleOrderedList().run()} />
          <B label={<Icon d={ICONS.check} />} title="Checklist" on={editor.isActive('taskList')} cmd={() => c().toggleTaskList().run()} />
          <Menu title="Alignment" label={<Icon d={align === 'center' ? ICONS.alignC : align === 'right' ? ICONS.alignR : ICONS.alignL} />}>
            {close => <>{([['left', 'Left', ICONS.alignL], ['center', 'Centre', ICONS.alignC], ['right', 'Right', ICONS.alignR]] as const).map(([a, l, d]) =>
              <button key={a} role="menuitem" className={`rt-item${align === a ? ' on' : ''}`} onMouseDown={e => e.preventDefault()}
                onClick={() => { c().setTextAlign(a).run(); close() }}><Icon d={d} /> {l}</button>)}</>}
          </Menu>
          <Sep />
          <Menu title="Insert" label="Insert">
            {close => <>
              <button role="menuitem" className="rt-item" onMouseDown={e => e.preventDefault()} onClick={() => { c().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(); close() }}>▦ Table</button>
              <button role="menuitem" className="rt-item" onMouseDown={e => e.preventDefault()} onClick={() => { c().toggleBlockquote().run(); close() }}><Icon d={ICONS.quote} /> Note / quote</button>
              <button role="menuitem" className="rt-item" onMouseDown={e => e.preventDefault()} onClick={() => { c().setHorizontalRule().run(); close() }}><Icon d={ICONS.rule} /> Divider</button>
              <button role="menuitem" className="rt-item" onMouseDown={e => e.preventDefault()} onClick={() => { c().insertContent(`<p><strong>${stamp()}</strong> </p>`).run(); close() }}><Icon d={ICONS.clock} /> Date &amp; time stamp</button>
            </>}
          </Menu>
          <Menu title="Link" on={editor.isActive('link')} label={<Icon d={ICONS.link} />}>
            {close => <form className="rt-link" onSubmit={e => {
              e.preventDefault()
              const url = String(new FormData(e.currentTarget).get('url') || '').trim()
              if (!url) c().extendMarkRange('link').unsetLink().run()
              else {
                const href = /^[a-z]+:/i.test(url) ? url : `https://${url}`
                if (/^(javascript|data|vbscript):/i.test(href)) return
                if (editor.state.selection.empty && !editor.isActive('link')) c().insertContent(`<a href="${href.replace(/"/g, '&quot;')}">${url.replace(/</g, '&lt;')}</a> `).run()
                else c().extendMarkRange('link').setLink({ href }).run()
              }
              close()
            }}>
              <input className="input" autoFocus placeholder="Paste a link (DOI, paper, SI…)" defaultValue={editor.getAttributes('link').href || ''} name="url" />
              <div className="row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                {editor.isActive('link') && <button type="button" className="btn ghost sm" onClick={() => { c().extendMarkRange('link').unsetLink().run(); close() }}>Remove</button>}
                <button className="btn primary sm">Apply</button>
              </div>
            </form>}
          </Menu>
          <Menu title="Insert symbol" label="Ω" wide>
            {close => <>{SYMBOLS.map(([group, list]) => <div key={group} className="rt-symgroup">
              <div className="rt-symhead">{group}</div>
              <div className="rt-syms">{list.map(s => <button key={s} role="menuitem" title={`Insert ${s}`} onMouseDown={e => e.preventDefault()}
                onClick={() => { c().insertContent(s).run(); close() }}>{s}</button>)}</div>
            </div>)}</>}
          </Menu>
          {['°C', '±', 'µ', '→', 'Δ'].map(s => <B key={s} label={s} title={`Insert ${s}`} cmd={() => c().insertContent(s).run()} />)}
          <Sep />
          <B label={<Icon d={ICONS.clear} />} title="Clear formatting" cmd={() => c().unsetAllMarks().clearNodes().run()} />
          {inTable && <div className="rt-table-tools" role="group" aria-label="Table">
            <span className="faint">Table:</span>
            <B label="+ Row" title="Add a row below" cmd={() => c().addRowAfter().run()} />
            <B label="+ Column" title="Add a column to the right" cmd={() => c().addColumnAfter().run()} />
            <B label="− Row" title="Delete this row" cmd={() => c().deleteRow().run()} />
            <B label="− Column" title="Delete this column" cmd={() => c().deleteColumn().run()} />
            <B label="Header" title="Toggle header row" cmd={() => c().toggleHeaderRow().run()} />
            <B label="Delete table" title="Delete the whole table" cmd={() => c().deleteTable().run()} />
          </div>}
        </div>
      )}
      <div className="prose"><EditorContent editor={editor} /></div>
    </div>
  )
}
