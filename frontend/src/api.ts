// Thin typed client for the Benchtop REST API.

export type Role = 'admin' | 'scientist' | 'viewer'
export type Status = 'open' | 'signed' | 'witnessed' | 'voided'
export type EntryKind = 'text' | 'reaction' | 'table' | 'file' | 'purification' | 'characterization' | 'safety' | 'screen'

export interface Preferences { theme?: 'system' | 'light' | 'dark'; default_notebook_id?: number | null; spectrum_expanded?: boolean; pinned?: number[]; dashboard?: { widgets: unknown[] } }
export interface User { id: number; username: string; full_name: string; email: string; role: Role; active: boolean; preferences: Preferences }
export interface UserBrief { id: number; username: string; full_name: string }
export interface Notebook {
  id: number; name: string; description: string; archived: boolean; owner: UserBrief
  experiment_count: number; status_counts: Record<Status, number>; created_at: string
}
export interface ExperimentSummary {
  id: number; eid: string; title: string; description: string; status: Status
  notebook_id: number | null; notebook_name: string | null; is_template: boolean
  template_id: number | null; tags: string[]; owner: UserBrief; created_at: string; updated_at: string
  match?: string; snippet?: string; score?: number; smiles?: string
}
export interface Entry {
  id: number; experiment_id: number; kind: EntryKind; title: string; position: number
  content: any; version: number; updated_by: number; updated_at: string
}
export interface Attachment { id: number; filename: string; content_type: string; size: number; sha256: string; created_at: string }
export interface Signature {
  id: number; meaning: string; comment: string; content_hash: string; signed_at: string; user: UserBrief
  signer_name?: string; signer_username?: string | null
  matches_current?: boolean
}
export interface Experiment extends ExperimentSummary {
  entries: Entry[]; attachments: Attachment[]; signatures: Signature[]
}
export interface StoichRow {
  role: 'reactant' | 'reagent' | 'catalyst' | 'solvent' | 'product'
  name: string; smiles: string; formula: string; mw: number | null
  material_id: number | null; lot_id: number | null
  equiv: number | null; mmol: number | null; mass_mg: number | null; volume_ml: number | null
  density: number | null; conc_m: number | null; purity: number | null
  limiting: boolean; basis: 'mass' | 'volume' | 'equiv' | 'mmol' | 'fill'
  actual_mass_mg: number | null; theoretical_mass_mg: number | null; yield_pct: number | null; notes: string
}
export interface Lot {
  id: number; material_id: number; barcode: string; supplier: string; catalog_number: string
  purity: number | null; amount: number; unit: string; location: string; status: string
  received_at: string; expires_at: string | null
  material_name?: string; mid?: string; smiles?: string; mw?: number | null
}
export interface Material {
  id: number; mid: string; name: string; kind: string; smiles: string; cas: string; formula: string
  mw: number | null; inchikey: string; density: number | null; synonyms: string; hazards: string; notes: string
  lot_count: number; available_lots: number; lots?: Lot[]; safety?: any; pubchem_cid?: number | null; source_experiment?: { id: number; eid: string; title: string } | null
  used_in?: { id: number; eid: string; title: string; status: Status }[]; created_by_user?: UserBrief; created_at: string
  score?: number
}
export interface ReactionSummary {
  experiment_id: number; eid: string; title: string; status: Status; owner: UserBrief; tags: string[]; date: string
  entry_title: string; reaction_smiles: string; temperature_c: number | null; temperature_label: string | null
  time_h: number | null; atmosphere: string | null; concentration_m: number | null; limiting: string | null
  scale_mmol: number | null; reagents: string[]; solvents: string[]; product: string | null; yield_pct: number | null
  purification: string | null; characterised: string[]
  metrics?: GreenMetrics
}
export interface GreenMetrics { scope?: string; atom_economy_pct?: number; rme_pct?: number; e_factor?: number; pmi?: number; solvent_l_per_kg?: number; missing_mass?: string[] }
export const PURIFICATION_LABELS: Record<string, string> = {
  column: 'Flash column', prep_tlc: 'Prep TLC', prep_hplc: 'Prep HPLC', recrystallization: 'Recrystallisation',
  trituration: 'Trituration', distillation: 'Distillation', extraction: 'Extraction only', none: 'Used crude',
}
export interface AuditEvent { id: number; ts: string; username: string; action: string; object_type: string; object_id: number | null; details: any; hash: string }

const TOKEN_KEY = 'benchtop.token'
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) } catch { return null } }
export const setToken = (t: string | null) => {
  try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) } catch { /* private mode */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

let onUnauthorized: () => void = () => {}
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn }

async function request<T>(method: string, path: string, body?: unknown, raw = false): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let payload: BodyInit | undefined
  if (body instanceof FormData) payload = body
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  const res = await fetch(path, { method, headers, body: payload })
  if (res.status === 401 && path !== '/api/auth/login') onUnauthorized()
  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = await res.json()
      msg = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail.map((d: any) => d.msg).join('; ') : msg
    } catch { /* not json */ }
    throw new ApiError(res.status, msg)
  }
  if (raw) return res as unknown as T
  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') || ''
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>
}

export const api = {
  get: <T>(p: string) => request<T>('GET', p),
  post: <T>(p: string, b?: unknown) => request<T>('POST', p, b ?? {}),
  put: <T>(p: string, b?: unknown) => request<T>('PUT', p, b),
  patch: <T>(p: string, b?: unknown) => request<T>('PATCH', p, b),
  del: <T>(p: string) => request<T>('DELETE', p),
  raw: (p: string) => request<Response>('GET', p, undefined, true),
}

export const depictUrl = (smiles: string, w = 260, h = 160, rxn = false, highlight = '') =>
  `/api/chem/depict?smiles=${encodeURIComponent(smiles)}&w=${w}&h=${h}${rxn ? '&rxn=true' : ''}${highlight ? `&highlight=${encodeURIComponent(highlight)}` : ''}`

export async function downloadAuthed(path: string, filename: string, open = false) {
  const res = await api.raw(path)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  if (open) window.open(url, '_blank')
  else {
    const a = document.createElement('a')
    a.href = url; a.download = filename; a.click()
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export const fmt = (v: number | null | undefined, nd = 3) =>
  v === null || v === undefined || Number.isNaN(v) ? '' : String(Number(v.toFixed(nd)))

export const when = (iso: string) => {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export const displayName = (u?: UserBrief | null) => (u ? u.full_name || u.username : '—')
