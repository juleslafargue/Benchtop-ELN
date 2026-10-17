import { useEffect, useState } from 'react'
import { api } from '../api'

/** New-password input with live feedback from the same rules the server enforces.
 *  `onValid` tells the parent whether the current value would be accepted. */
export default function PasswordField({ label = 'New password', value, onChange, onValid, username = '', fullName = '', email = '', autoFocus }: {
  label?: string; value: string; onChange: (v: string) => void; onValid?: (ok: boolean) => void
  username?: string; fullName?: string; email?: string; autoFocus?: boolean
}) {
  const [problems, setProblems] = useState<string[] | null>(null)
  const [show, setShow] = useState(false)
  useEffect(() => {
    if (!value) { setProblems(null); onValid?.(false); return }
    let alive = true
    const t = setTimeout(() => {
      api.post<{ problems: string[] }>('/api/auth/password-check', { password: value, username, full_name: fullName, email })
        .then(r => { if (alive) { setProblems(r.problems); onValid?.(r.problems.length === 0) } })
        .catch(() => { if (alive) { setProblems(null); onValid?.(value.length >= 10) } })
    }, 250)
    return () => { alive = false; clearTimeout(t) }
  }, [value, username, fullName, email]) // eslint-disable-line react-hooks/exhaustive-deps
  const strength = !value ? 0 : problems && problems.length ? 1 : value.length >= 16 ? 3 : 2
  return (
    <div className="field pw-field">
      <span>{label}</span>
      <div className="pw-row">
        <input className="input" type={show ? 'text' : 'password'} autoComplete="new-password" autoFocus={autoFocus} value={value}
          onChange={e => onChange(e.target.value)} aria-describedby="pw-help" aria-invalid={!!problems?.length} />
        <button type="button" className="btn ghost sm" onClick={() => setShow(s => !s)} aria-pressed={show}>{show ? 'Hide' : 'Show'}</button>
      </div>
      <div className={`pw-meter s${strength}`} aria-hidden="true"><i /><i /><i /></div>
      <div id="pw-help" className="pw-help" aria-live="polite">
        {!value ? <span className="faint">At least 10 characters. A few unrelated words together is long, strong and easy to remember.</span>
          : problems === null ? <span className="faint">Checking…</span>
            : problems.length ? <span className="warn-text">{problems[0]}.</span>
              : <span className="ok-text">{strength === 3 ? 'Strong password.' : 'Good. Longer is even better.'}</span>}
      </div>
    </div>
  )
}
