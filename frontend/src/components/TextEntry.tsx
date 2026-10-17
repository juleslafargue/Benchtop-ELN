import { useEffect } from 'react'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Subscript from '@tiptap/extension-subscript'
import Superscript from '@tiptap/extension-superscript'
import Table from '@tiptap/extension-table'
import TableRow from '@tiptap/extension-table-row'
import TableCell from '@tiptap/extension-table-cell'
import TableHeader from '@tiptap/extension-table-header'
import Placeholder from './placeholder'
import type { EntryEditorProps } from './EntryCard'

export default function TextEntry({ content, onChange, readOnly }: EntryEditorProps) {
  const editor = useEditor({
    extensions: [StarterKit, Subscript, Superscript, Table.configure({ resizable: false }), TableRow, TableCell, TableHeader,
      Placeholder('Write here. Ctrl+, for subscript, Ctrl+. for superscript.')],
    content: content.html || '',
    editable: !readOnly,
    onUpdate: ({ editor }) => onChange({ ...content, html: editor.getHTML() }),
  }, [readOnly])

  // pick up server-normalised HTML without clobbering the caret during typing
  useEffect(() => {
    if (editor && !editor.isFocused && content.html !== editor.getHTML()) editor.commands.setContent(content.html || '', false)
  }, [content.html, editor])

  if (!editor) return null
  const B = ({ cmd, on, label, title }: { cmd: () => void; on?: boolean; label: string; title: string }) => (
    <button type="button" className={on ? 'on' : ''} title={title} aria-label={title} aria-pressed={!!on}
      onMouseDown={e => e.preventDefault()} onClick={cmd}>{label}</button>
  )
  const c = () => editor.chain().focus()
  return (
    <div>
      {!readOnly && (
        <div className="rt-toolbar" role="toolbar" aria-label="Formatting">
          <B label="B" title="Bold" on={editor.isActive('bold')} cmd={() => c().toggleBold().run()} />
          <B label="I" title="Italic" on={editor.isActive('italic')} cmd={() => c().toggleItalic().run()} />
          <B label="x₂" title="Subscript" on={editor.isActive('subscript')} cmd={() => c().toggleSubscript().run()} />
          <B label="x²" title="Superscript" on={editor.isActive('superscript')} cmd={() => c().toggleSuperscript().run()} />
          <B label="H" title="Heading" on={editor.isActive('heading')} cmd={() => c().toggleHeading({ level: 3 }).run()} />
          <B label="• List" title="Bulleted list" on={editor.isActive('bulletList')} cmd={() => c().toggleBulletList().run()} />
          <B label="1. Steps" title="Numbered list" on={editor.isActive('orderedList')} cmd={() => c().toggleOrderedList().run()} />
          <B label="Table" title="Insert table" cmd={() => c().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()} />
          {['°C', '±', 'µ', 'δ', '→', 'Δ'].map(s => <B key={s} label={s} title={`Insert ${s}`} cmd={() => c().insertContent(s).run()} />)}
        </div>
      )}
      <div className="prose"><EditorContent editor={editor} /></div>
    </div>
  )
}
