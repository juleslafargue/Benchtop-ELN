// Minimal placeholder extension (avoids an extra dependency).
import { Extension } from '@tiptap/core'
import { Plugin } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

export default function Placeholder(text: string) {
  return Extension.create({
    name: 'benchtopPlaceholder',
    addProseMirrorPlugins() {
      return [new Plugin({
        props: {
          decorations: ({ doc }) => {
            const first = doc.firstChild
            if (doc.childCount !== 1 || !first || !first.isTextblock || first.content.size !== 0 || !this.editor.isEditable) return null
            return DecorationSet.create(doc, [Decoration.node(0, first.nodeSize, { class: 'is-editor-empty', 'data-placeholder': text })])
          },
        },
      })]
    },
  })
}
