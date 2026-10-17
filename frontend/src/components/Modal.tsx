import { useEffect, useId, useRef, type ReactNode } from 'react'

export default function Modal({ title, children, footer, onClose, wide }: {
  title: ReactNode; children: ReactNode; footer?: ReactNode; onClose: () => void; wide?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const d = ref.current!
    if (!d.open) d.showModal()
    const cancel = (e: Event) => { e.preventDefault(); onClose() }
    d.addEventListener('cancel', cancel)
    return () => d.removeEventListener('cancel', cancel)
  }, [onClose])
  return (
    <dialog ref={ref} className={`modal${wide ? ' wide' : ''}`} aria-labelledby={titleId}>
      <div className="modal-head"><h2 id={titleId} style={{ fontSize: 19 }}>{title}</h2></div>
      <div className="modal-body">{children}</div>
      {footer && <div className="modal-foot">{footer}</div>}
    </dialog>
  )
}
