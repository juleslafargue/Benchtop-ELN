import { useEffect, useState } from 'react'
import Logo from './Logo'

/** The installed app's title bar strip, shown only with Chrome's compact title bar (<html data-wco>):
 *  Chrome's own buttons sit at its right, and the window is dragged by it. */
export default function TitleBar() {
  const [title, setTitle] = useState(document.title)
  useEffect(() => {
    const el = document.querySelector('title')
    if (!el) return
    const obs = new MutationObserver(() => setTitle(document.title))
    obs.observe(el, { childList: true, characterData: true, subtree: true })
    return () => obs.disconnect()
  }, [])
  return (
    <div className="wco-bar" aria-hidden="true">
      <Logo size={14} />
      <span className="wco-title">{title}</span>
    </div>
  )
}
