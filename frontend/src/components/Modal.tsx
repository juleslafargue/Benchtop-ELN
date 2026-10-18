import { useEffect, useId, useRef, type ReactNode } from 'react'

// While any dialog is open the page behind it must not scroll (a wheel or trackpad gesture inside the
// structure editor used to move the experiment page underneath). Counted, so nested dialogs work.
let locks = 0
let savedY = 0
function lockPage() {
  if (locks++ === 0) {
    // pin the page where it is (overflow:hidden alone jumps back to the top in some browsers)
    savedY = window.scrollY
    const w = window.innerWidth - document.documentElement.clientWidth // keep the layout from jumping when the scrollbar goes
    const b = document.body.style
    document.documentElement.style.setProperty('--scrollbar-gap', `${w}px`)
    b.position = 'fixed'; b.top = `-${savedY}px`; b.left = '0'; b.right = '0'
    document.documentElement.classList.add('modal-open')
  }
  return () => {
    if (--locks === 0) {
      const b = document.body.style
      b.position = ''; b.top = ''; b.left = ''; b.right = ''
      document.documentElement.classList.remove('modal-open')
      window.scrollTo(0, savedY)
    }
  }
}

export default function Modal({ title, children, footer, onClose, wide }: {
  title: ReactNode; children: ReactNode; footer?: ReactNode; onClose: () => void; wide?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(lockPage, [])
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
