import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode; label: string; resetKey?: unknown; raw?: unknown; page?: boolean }
interface State { error: Error | null }

/** If something inside can't be displayed, show a small note instead of a blank page.
 *  Used around each experiment section and around each page. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }
  static getDerivedStateFromError(error: Error): State { return { error } }
  componentDidCatch(error: Error) { console.warn(`Benchtop couldn't display ${this.props.label}:`, error) }
  componentDidUpdate(prev: Props) { if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null }) }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const copy = () => {
      try { navigator.clipboard.writeText(JSON.stringify(this.props.raw, null, 2)) } catch { /* no clipboard */ }
    }
    if (this.props.page) return (
      <div className="panel crash" role="alert">
        <h2>This page couldn't be displayed</h2>
        <p className="faint">Your data is safe; this is a display problem. Reloading usually fixes it. If it keeps happening, the message below helps track it down.</p>
        <code className="crash-msg">{error.message}</code>
        <div className="row"><button className="btn primary" onClick={() => location.reload()}>Reload</button>
          <a className="btn" href="/">Go to the dashboard</a></div>
      </div>
    )
    return (
      <div className="crash" role="alert">
        <p><b>This section couldn't be displayed.</b> <span className="faint">The data is still stored and still in exports; it may have been saved by an older version or a script in a shape this screen doesn't expect.</span></p>
        <code className="crash-msg">{error.message}</code>
        {this.props.raw !== undefined && <div><button className="btn sm" onClick={copy}>Copy the raw data</button></div>}
      </div>
    )
  }
}
