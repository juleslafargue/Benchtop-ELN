import { useApp } from '../App'
import type { Audience, Visibility } from '../api'

export interface AudienceValue { visibility: Visibility; group_id: number | null }

export const AudIcon = ({ v }: { v: Visibility }) => (
  <svg className="aud-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    {v === 'private' ? <><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" /><path d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7" /></>
      : v === 'group' ? <><circle cx="6" cy="6" r="2.2" /><path d="M2 13c.4-2.2 2-3.4 4-3.4s3.6 1.2 4 3.4" /><circle cx="11.3" cy="6.6" r="1.8" /><path d="M11 9.7c1.6.1 2.7 1.1 3 3" /></>
        : <><circle cx="8" cy="8" r="5.5" /><path d="M2.5 8h11M8 2.5c1.6 1.6 2.3 3.5 2.3 5.5S9.6 11.9 8 13.5M8 2.5C6.4 4.1 5.7 6 5.7 8s.7 3.9 2.3 5.5" /></>}
  </svg>
)

export function audienceText(a: Audience | { visibility: Visibility; group: { name: string } | null }) {
  if (a.visibility === 'group') return a.group ? `${a.group.name} only` : 'Group only'
  if (a.visibility === 'private') return a.group ? `Private (+ ${a.group.name} leads)` : 'Private'
  return 'Everyone'
}

export function AudienceBadge({ audience, title }: { audience: Audience | { visibility: Visibility; group: { id: number; name: string } | null }; title?: string }) {
  const tip = title || (audience.visibility === 'everyone' ? 'Everyone with a Benchtop account can open this'
    : audience.visibility === 'group' ? `Only members of ${audience.group?.name || 'the group'} can open this`
      : `Only the owner${audience.group ? ` and ${audience.group.name}'s leads` : ''} can open this`)
  return <span className={`aud-badge aud-${audience.visibility}`} title={tip}><AudIcon v={audience.visibility} />{audienceText(audience)}</span>
}

/** Who can open a notebook: everyone, one of my groups, or only me (and my group's leads). */
export function AudiencePicker({ value, onChange }: { value: AudienceValue; onChange: (v: AudienceValue) => void }) {
  const { groups, user } = useApp()
  const mine = groups.filter(g => g.my_role || user.role === 'admin')
  const options: { key: string; v: AudienceValue; label: string; hint: string; icon: Visibility }[] = [
    ...mine.map(g => ({ key: `g${g.id}`, v: { visibility: 'group' as Visibility, group_id: g.id }, label: g.name,
      hint: `The ${g.members.length} member${g.members.length === 1 ? '' : 's'} of ${g.name}`, icon: 'group' as Visibility })),
    { key: 'everyone', v: { visibility: 'everyone', group_id: null }, label: 'Everyone', hint: 'Anyone with a Benchtop account', icon: 'everyone' },
    { key: 'private', v: { visibility: 'private', group_id: value.visibility === 'private' ? value.group_id : (mine.find(g => g.my_role)?.id ?? null) },
      label: 'Only me', hint: '', icon: 'private' },
  ]
  const cur = value.visibility === 'group' ? `g${value.group_id}` : value.visibility
  const privGroup = mine.find(g => g.id === value.group_id)
  return (
    <div className="aud-picker" role="radiogroup" aria-label="Who can open it">
      {options.map(o => (
        <label key={o.key} className={`aud-opt${cur === o.key ? ' on' : ''}`}>
          <input type="radio" name="audience" checked={cur === o.key} onChange={() => onChange(o.v)} />
          <AudIcon v={o.icon} />
          <span><b>{o.label}</b><span className="faint">{o.key === 'private'
            ? (value.visibility === 'private' && privGroup ? `You, plus ${privGroup.name}'s leads` : 'Just you (and admins)')
            : o.hint}</span></span>
        </label>
      ))}
      {value.visibility === 'private' && mine.some(g => g.my_role) && (
        <label className="field aud-leads">Group leads who can also see it
          <select className="select" value={value.group_id ?? ''} onChange={e => onChange({ visibility: 'private', group_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">Nobody else</option>
            {mine.filter(g => g.my_role).map(g => <option key={g.id} value={g.id}>{g.name}'s leads</option>)}
          </select>
        </label>
      )}
      {mine.length === 0 && <p className="faint" style={{ margin: 0 }}>You're not in a group yet. An admin can create groups on the Groups page.</p>}
    </div>
  )
}

export const defaultAudience = (groups: { id: number; my_role: string | null }[]): AudienceValue => {
  const g = groups.find(x => x.my_role)
  return g ? { visibility: 'group', group_id: g.id } : { visibility: 'everyone', group_id: null }
}
