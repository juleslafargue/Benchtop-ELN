// Minimal JCAMP-DX 4.24/5.x reader (XYDATA and XYPOINTS/PEAK TABLE blocks), plus CSV/TSV.
// Handles AFFN and the ASDF compressions (SQZ, DIF, DUP) used by most instrument exports.

export interface Spectrum {
  title: string
  x: Float64Array
  y: Float64Array
  xLabel: string
  yLabel: string
  kind: 'nmr' | 'ir' | 'uv' | 'ms' | 'other'
  reversed: boolean // draw high→low (NMR ppm, IR wavenumber)
  dips: boolean     // peaks point down (transmittance)
  nucleus?: string
  frequency?: number
  solvent?: string
  isStick: boolean  // peak table / MS: draw as sticks
}

const SQZ: Record<string, number> = { '@': 0, A: 1, B: 2, C: 3, D: 4, E: 5, F: 6, G: 7, H: 8, I: 9, a: -1, b: -2, c: -3, d: -4, e: -5, f: -6, g: -7, h: -8, i: -9 }
const DIF: Record<string, number> = { '%': 0, J: 1, K: 2, L: 3, M: 4, N: 5, O: 6, P: 7, Q: 8, R: 9, j: -1, k: -2, l: -3, m: -4, n: -5, o: -6, p: -7, q: -8, r: -9 }
const DUP: Record<string, number> = { S: 1, T: 2, U: 3, V: 4, W: 5, X: 6, Y: 7, Z: 8, s: 9 }

type Tok = { kind: 'abs' | 'dif' | 'dup'; text: string }

function tokenize(line: string): Tok[] {
  const toks: Tok[] = []
  let cur: Tok | null = null
  const push = () => { if (cur) toks.push(cur); cur = null }
  for (const ch of line) {
    if (ch === ' ' || ch === ',' || ch === '\t' || ch === ';') { push(); continue }
    if ((ch >= '0' && ch <= '9') || ch === '.') {
      if (!cur) cur = { kind: 'abs', text: '' }
      cur.text += ch
    } else if (ch === '+' || ch === '-') {
      push(); cur = { kind: 'abs', text: ch === '-' ? '-' : '' }
    } else if (ch in SQZ) {
      push(); const v = SQZ[ch]; cur = { kind: 'abs', text: (v < 0 ? '-' : '') + Math.abs(v) }
    } else if (ch in DIF) {
      push(); const v = DIF[ch]; cur = { kind: 'dif', text: (v < 0 ? '-' : '') + Math.abs(v) }
    } else if (ch in DUP) {
      push(); cur = { kind: 'dup', text: String(DUP[ch]) }
    } else if (ch === '?') {
      push(); toks.push({ kind: 'abs', text: 'NaN' })
    }
  }
  push()
  return toks
}

/** Returns [x, ys, endsInDif] for one XYDATA line. */
function parseLine(line: string): [number, number[], boolean] {
  if (/^[\s\d.,+\-eE]+$/.test(line)) { // plain AFFN, possibly with exponents
    const nums = line.trim().split(/[\s,]+/).filter(Boolean).map(Number)
    return [nums[0], nums.slice(1), false]
  }
  const toks = tokenize(line)
  if (!toks.length) return [NaN, [], false]
  const x = Number(toks[0].text)
  const ys: number[] = []
  let last: Tok | null = null
  let endsDif = false
  for (const t of toks.slice(1)) {
    if (t.kind === 'abs') { ys.push(Number(t.text)); last = t; endsDif = false }
    else if (t.kind === 'dif') { ys.push((ys.length ? ys[ys.length - 1] : 0) + Number(t.text)); last = t; endsDif = true }
    else if (t.kind === 'dup' && last) {
      for (let k = 1; k < Number(t.text); k++) {
        if (last.kind === 'dif') ys.push(ys[ys.length - 1] + Number(last.text))
        else ys.push(Number(last.text))
      }
    }
  }
  return [x, ys, endsDif]
}

function labels(raw: string): Map<string, string> {
  const m = new Map<string, string>()
  const re = /^##([^=]+)=(.*)$/gm
  let r: RegExpExecArray | null
  while ((r = re.exec(raw))) {
    const key = r[1].replace(/[\s\-/_]/g, '').toUpperCase()
    if (!m.has(key)) m.set(key, r[2].trim())
  }
  return m
}

export function parseJcamp(raw: string): Spectrum {
  const text = raw.replace(/\$\$.*$/gm, '') // strip comments
  const L = labels(text)
  if (L.has('NTUPLES')) {
    throw new Error('This file holds raw or complex data (NTUPLES). Export the processed real spectrum as JCAMP-DX XYDATA from your NMR software, then attach that.')
  }
  const num = (k: string) => (L.has(k) ? Number(L.get(k)!.replace(/[^\d.eE+-]/g, '')) : undefined)
  const xFactor = num('XFACTOR') ?? 1
  const yFactor = num('YFACTOR') ?? 1
  const xUnits = (L.get('XUNITS') || '').toUpperCase()
  const yUnits = (L.get('YUNITS') || '').toUpperCase()
  const dataType = (L.get('DATATYPE') || '').toUpperCase()
  const freq = num('.OBSERVEFREQUENCY')
  const lines = text.split(/\r?\n/)

  let xs: number[] = []
  let ys: number[] = []
  let isStick = false
  const xyIdx = lines.findIndex(l => /^##XYDATA\s*=/i.test(l))
  const ptIdx = lines.findIndex(l => /^##(XYPOINTS|PEAK\s*TABLE)\s*=/i.test(l))
  if (xyIdx >= 0) {
    let prevDif = false
    const xFirsts: number[] = []
    for (let i = xyIdx + 1; i < lines.length && !lines[i].startsWith('##'); i++) {
      const line = lines[i].trim()
      if (!line) continue
      const [x, vals, endsDif] = parseLine(line)
      const v = prevDif ? vals.slice(1) : vals // DIF check value repeats the previous line's last Y
      xFirsts.push(x)
      for (const y of v) ys.push(y * yFactor)
      prevDif = endsDif
    }
    const firstX = num('FIRSTX') ?? xFirsts[0] * xFactor
    const lastX = num('LASTX') ?? (xFirsts[xFirsts.length - 1] * xFactor)
    const n = ys.length
    const step = n > 1 ? (lastX - firstX) / (n - 1) : 0
    xs = Array.from({ length: n }, (_, i) => firstX + i * step)
  } else if (ptIdx >= 0) {
    isStick = /PEAK/i.test(lines[ptIdx]) || dataType.includes('MASS')
    const nums: number[] = []
    for (let i = ptIdx + 1; i < lines.length && !lines[i].startsWith('##'); i++) {
      for (const t of lines[i].split(/[\s,;]+/)) if (t) nums.push(Number(t))
    }
    for (let i = 0; i + 1 < nums.length; i += 2) { xs.push(nums[i] * xFactor); ys.push(nums[i + 1] * yFactor) }
  } else {
    throw new Error('No ##XYDATA or ##PEAK TABLE block found in this JCAMP-DX file.')
  }

  let xLabel = xUnits || 'x'
  if (xUnits === 'HZ' && freq) { xs = xs.map(v => v / freq); xLabel = 'PPM' }
  const kind: Spectrum['kind'] = dataType.includes('NMR') || xLabel === 'PPM' ? 'nmr'
    : dataType.includes('INFRARED') || xUnits.includes('1/CM') ? 'ir'
    : dataType.includes('MASS') ? 'ms' : dataType.includes('UV') ? 'uv' : 'other'
  if (kind === 'ms') isStick = true
  return finish({
    title: L.get('TITLE') || 'Spectrum', xLabel: kind === 'nmr' ? 'δ (ppm)' : kind === 'ir' ? 'Wavenumber (cm⁻¹)' : xLabel,
    yLabel: yUnits || 'Intensity', kind, reversed: kind === 'nmr' || kind === 'ir', dips: yUnits.includes('TRANSMITTANCE'),
    nucleus: L.get('.OBSERVENUCLEUS')?.replace(/\^/g, ''), frequency: freq, solvent: L.get('.SOLVENTNAME'), isStick,
  }, xs, ys)
}

export function parseDelimited(raw: string, name: string): Spectrum {
  const xs: number[] = []
  const ys: number[] = []
  for (const line of raw.split(/\r?\n/)) {
    const parts = line.trim().split(/[\s,;]+/)
    if (parts.length < 2) continue
    const a = Number(parts[0]), b = Number(parts[1])
    if (Number.isFinite(a) && Number.isFinite(b)) { xs.push(a); ys.push(b) }
  }
  if (xs.length < 2) throw new Error('Expected two numeric columns (x, y).')
  const ppmLike = Math.min(...xs) > -2 && Math.max(...xs) < 250 && /nmr|1h|13c|ppm/i.test(name)
  const irLike = Math.min(...xs) >= 350 && Math.max(...xs) <= 4500 && /ir|ftir/i.test(name)
  return finish({
    title: name, xLabel: ppmLike ? 'δ (ppm)' : irLike ? 'Wavenumber (cm⁻¹)' : 'x', yLabel: 'Intensity',
    kind: ppmLike ? 'nmr' : irLike ? 'ir' : 'other', reversed: ppmLike || irLike, dips: false, isStick: false,
  }, xs, ys)
}

function finish(meta: Omit<Spectrum, 'x' | 'y'>, xs: number[], ys: number[]): Spectrum {
  // store ascending in x for simpler maths; drawing handles reversal
  const idx = xs.map((_, i) => i)
  if (xs.length > 1 && xs[0] > xs[xs.length - 1]) idx.reverse()
  return { ...meta, x: Float64Array.from(idx.map(i => xs[i])), y: Float64Array.from(idx.map(i => ys[i])) }
}

export const SPECTRUM_EXT = /\.(jdx|dx|jcamp|jcm|csv|tsv|txt|xy|zip)$/i

export function parseSpectrumFile(raw: string, filename: string): Spectrum {
  if (/^\s*##/.test(raw)) return parseJcamp(raw)
  return parseDelimited(raw, filename)
}

// ---------------------------------------------------------------- analysis

export function lowerBound(a: Float64Array, v: number) {
  let lo = 0, hi = a.length
  while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < v) lo = m + 1; else hi = m }
  return lo
}

/** Area between x0 and x1 (trapezoid rule, absolute x). */
export function integrate(s: Spectrum, x0: number, x1: number) {
  const a = Math.min(x0, x1), b = Math.max(x0, x1)
  const i0 = lowerBound(s.x, a), i1 = Math.min(lowerBound(s.x, b), s.x.length - 1)
  let area = 0
  for (let i = i0; i < i1; i++) area += (s.x[i + 1] - s.x[i]) * (s.y[i] + s.y[i + 1]) / 2
  return area
}

/** Local maxima (or minima for transmittance) above `frac` of the range's extreme. */
export function pickPeaks(s: Spectrum, x0: number, x1: number, frac: number, limit = 60) {
  const i0 = lowerBound(s.x, Math.min(x0, x1)), i1 = Math.min(lowerBound(s.x, Math.max(x0, x1)), s.x.length - 1)
  if (s.isStick) {
    const out: { x: number; y: number }[] = []
    let max = 0
    for (let i = i0; i <= i1; i++) max = Math.max(max, s.y[i])
    for (let i = i0; i <= i1; i++) if (s.y[i] >= frac * max) out.push({ x: s.x[i], y: s.y[i] })
    return out.sort((a, b) => b.y - a.y).slice(0, limit)
  }
  const sign = s.dips ? -1 : 1
  let lo = Infinity, hi = -Infinity
  for (let i = i0; i <= i1; i++) { const v = sign * s.y[i]; if (v < lo) lo = v; if (v > hi) hi = v }
  const cut = lo + frac * (hi - lo)
  const out: { x: number; y: number }[] = []
  for (let i = Math.max(i0, 2); i <= Math.min(i1, s.x.length - 3); i++) {
    const v = sign * s.y[i]
    if (v > cut && v >= sign * s.y[i - 1] && v > sign * s.y[i + 1] && v >= sign * s.y[i - 2] && v > sign * s.y[i + 2]) {
      // parabolic interpolation for sub-point accuracy
      const a = sign * s.y[i - 1], c = sign * s.y[i + 1]
      const d = (a - c) / (2 * (a - 2 * v + c) || 1)
      out.push({ x: s.x[i] + d * (s.x[i + 1] - s.x[i]), y: s.y[i] })
    }
  }
  return out.sort((a, b) => sign * (b.y - a.y)).slice(0, limit)
}

// ---------------------------------------------------------------- multiplets (first-order)

export interface Multiplet {
  shift: number          // ppm (centre)
  lo: number; hi: number // ppm range of the signal
  mult: string           // s, d, t, q, p, dd, dt, td, m
  j: number[]            // Hz, largest first
}

const near = (a: number, b: number, tolHz: number) => Math.abs(a - b) <= Math.max(tolHz, 0.12 * Math.max(a, b))

/** Classify the lines inside [a, b] (ppm) as a first-order multiplet. */
export function analyzeMultiplet(s: Spectrum, a: number, b: number): Multiplet {
  const lo = Math.min(a, b), hi = Math.max(a, b)
  const freq = s.frequency || 400
  const lines = pickPeaks(s, lo, hi, 0.12, 40).sort((p, q) => p.x - q.x)
  const n = lines.length
  const xs = lines.map(l => l.x), ys = lines.map(l => l.y)
  const centre = n ? (xs[0] + xs[n - 1]) / 2 : (lo + hi) / 2
  const out = (mult: string, j: number[] = []): Multiplet => ({ shift: centre, lo: n ? xs[0] : lo, hi: n ? xs[n - 1] : hi, mult, j: j.map(v => Math.round(v * 10) / 10).sort((p, q) => q - p) })
  if (n <= 1) return { shift: n ? xs[0] : (lo + hi) / 2, lo, hi, mult: 's', j: [] }
  const gaps = xs.slice(1).map((x, i) => (x - xs[i]) * freq) // Hz
  const tol = 0.7
  const allEqual = gaps.every(g => near(g, gaps[0], tol))
  const mean = gaps.reduce((p, q) => p + q, 0) / gaps.length
  const ratio = (i: number, k: number) => ys[i] / ys[k]
  if (n === 2) return near(ys[0], ys[1], 0) || (ratio(0, 1) > 0.5 && ratio(0, 1) < 2) ? out('d', [gaps[0]]) : out('m')
  if (n === 3 && allEqual) {
    const r = (ys[1] * 2) / (ys[0] + ys[2])
    if (r > 1.3 && r < 3) return out('t', [mean])
  }
  if (n === 4) {
    if (allEqual) {
      const r = (ys[1] + ys[2]) / (ys[0] + ys[3])
      if (r > 2 && r < 4.5) return out('q', [mean])
    }
    if (near(gaps[0], gaps[2], tol) && ratio(0, 3) > 0.5 && ratio(0, 3) < 2) {
      const small = (gaps[0] + gaps[2]) / 2
      const large = (xs[2] - xs[0]) * freq
      if (large > small + tol) return out('dd', [large, small])
    }
  }
  if (n === 5 && allEqual) return out('p', [mean])
  if (n === 6) {
    // dt: two triplets  |  td: three doublets
    const t1 = [gaps[0], gaps[1]], t2 = [gaps[3], gaps[4]]
    if (near(t1[0], t1[1], tol) && near(t2[0], t2[1], tol) && near(t1[0], t2[0], tol)) {
      return out('dt', [(xs[3] - xs[0]) * freq, (t1[0] + t2[0]) / 2])
    }
    if (near(gaps[0], gaps[2], tol) && near(gaps[2], gaps[4], tol) && near(gaps[1], gaps[3], tol)) {
      return out('td', [(xs[2] - xs[0]) * freq, gaps[0]])
    }
  }
  return out('m')
}

export function formatSignal(m: Multiplet, nH: number | null, decimals = 2) {
  const shift = m.mult === 'm'
    ? `${m.hi.toFixed(decimals)}–${m.lo.toFixed(decimals)}`
    : m.shift.toFixed(decimals)
  const parts = [m.mult]
  if (m.j.length) parts.push(`J = ${m.j.map(j => j.toFixed(1)).join(', ')} Hz`)
  if (nH !== null) parts.push(`${Number(nH.toFixed(nH < 1.5 ? 1 : 0))}H`)
  return `${shift} (${parts.join(', ')})`
}

// ---------------------------------------------------------------- solvents & referencing

export interface Marker { label: string; ppm: number }

// Residual solvent and water signals (Fulmer et al., Organometallics 2010, 29, 2176)
const H1: Record<string, Marker[]> = {
  CDCL3: [{ label: 'CHCl₃', ppm: 7.26 }, { label: 'H₂O', ppm: 1.56 }],
  DMSO: [{ label: 'DMSO-d₅', ppm: 2.50 }, { label: 'H₂O', ppm: 3.33 }],
  CD3OD: [{ label: 'CHD₂OD', ppm: 3.31 }, { label: 'H₂O', ppm: 4.87 }],
  C6D6: [{ label: 'C₆D₅H', ppm: 7.16 }, { label: 'H₂O', ppm: 0.40 }],
  D2O: [{ label: 'HDO', ppm: 4.79 }],
  CD2CL2: [{ label: 'CHDCl₂', ppm: 5.32 }, { label: 'H₂O', ppm: 1.52 }],
  ACETONE: [{ label: 'acetone-d₅', ppm: 2.05 }, { label: 'H₂O', ppm: 2.84 }],
  CD3CN: [{ label: 'CHD₂CN', ppm: 1.94 }, { label: 'H₂O', ppm: 2.13 }],
}
const C13: Record<string, Marker[]> = {
  CDCL3: [{ label: 'CDCl₃', ppm: 77.16 }], DMSO: [{ label: 'DMSO-d₆', ppm: 39.52 }], CD3OD: [{ label: 'CD₃OD', ppm: 49.00 }],
  C6D6: [{ label: 'C₆D₆', ppm: 128.06 }], CD2CL2: [{ label: 'CD₂Cl₂', ppm: 53.84 }],
  ACETONE: [{ label: 'acetone-d₆', ppm: 29.84 }, { label: 'acetone-d₆', ppm: 206.26 }], CD3CN: [{ label: 'CD₃CN', ppm: 1.32 }, { label: 'CD₃CN', ppm: 118.26 }],
}

function solventKey(solvent?: string) {
  const s = (solvent || '').toUpperCase().replace(/[\s_]/g, '')
  if (!s) return ''
  if (s.startsWith('DMSO')) return 'DMSO'
  if (s.startsWith('ACETONE')) return 'ACETONE'
  if (s.includes('CHLOROFORM')) return 'CDCL3'
  if (s.includes('METHANOL')) return 'CD3OD'
  if (s.includes('BENZENE')) return 'C6D6'
  return s.replace(/-D\d+$/, '')
}

export function solventMarkers(s: Spectrum): Marker[] {
  if (s.kind !== 'nmr') return []
  const table = /13C/i.test(s.nucleus || '') ? C13 : H1
  return table[solventKey(s.solvent)] || []
}

export function referenceChoices(s: Spectrum): Marker[] {
  const tms = { label: 'TMS', ppm: 0 }
  // water moves with temperature and concentration, so it isn't offered as a reference (except HDO in D2O)
  const ok = solventMarkers(s).filter(m => m.label !== 'H₂O' && (m.label !== 'HDO' || solventKey(s.solvent) === 'D2O'))
  return [...ok, tms]
}

export function shifted(s: Spectrum, offset: number): Spectrum {
  if (!offset) return s
  return { ...s, x: s.x.map(v => v + offset) }
}

/** Nearest local maximum (peak top) to x within ±win; falls back to x if there is none.
 *  Nearest rather than tallest, so clicking a small solvent peak next to a big multiplet works. */
export function snapToPeak(s: Spectrum, x: number, win: number) {
  const sign = s.dips ? -1 : 1
  const i0 = Math.max(1, lowerBound(s.x, x - win)), i1 = Math.min(lowerBound(s.x, x + win), s.x.length - 2)
  let lo = Infinity, hi = -Infinity
  for (let i = i0; i <= i1; i++) { const v = sign * s.y[i]; if (v < lo) lo = v; if (v > hi) hi = v }
  let best = -1
  for (let i = i0; i <= i1; i++) {
    const v = sign * s.y[i]
    if (v > lo + 0.02 * (hi - lo) && v >= sign * s.y[i - 1] && v > sign * s.y[i + 1]) {
      if (best < 0 || Math.abs(s.x[i] - x) < Math.abs(s.x[best] - x)) best = i
    }
  }
  return best < 0 ? x : s.x[best]
}

export const MULTIPLICITIES = ['s', 'br s', 'd', 't', 'q', 'p', 'sext', 'hept', 'dd', 'dt', 'td', 'ddd', 'dq', 'm']

// ---------------------------------------------------------------- Bruker processed data (zipped experiment folder)

function brukerParams(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of text.matchAll(/^##\$?([A-Za-z0-9_]+)=\s*(.*)$/gm)) out[m[1].toUpperCase()] = m[2].trim().replace(/^<(.*)>$/, '$1')
  return out
}

/** Read pdata/N/1r + procs (+ acqus for nucleus/solvent) from a zipped Bruker experiment. */
export async function parseBrukerZip(buf: ArrayBuffer, filename: string): Promise<Spectrum> {
  const { unzipSync, strFromU8 } = await import('fflate')
  const files = unzipSync(new Uint8Array(buf))
  const names = Object.keys(files)
  const real = names.filter(n => /(^|\/)pdata\/\d+\/1r$/.test(n)).sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b, undefined, { numeric: true }))
  if (!real.length) {
    const jdx = names.find(n => /\.(jdx|dx|jcamp)$/i.test(n))
    if (jdx) return parseJcamp(strFromU8(files[jdx]))
    if (names.some(n => /(^|\/)fid$/.test(n))) {
      throw new Error('This Bruker folder only has the raw FID. Process it once in TopSpin or MestReNova (so a pdata/1/1r file exists), or export a JCAMP-DX file, then add it again.')
    }
    throw new Error('No processed Bruker spectrum (pdata/…/1r) or JCAMP-DX file found in this zip.')
  }
  const path = real[0]
  const dir = path.slice(0, -2)
  const procs = files[dir + 'procs'] ? brukerParams(strFromU8(files[dir + 'procs'])) : null
  if (!procs) throw new Error('Found 1r but not its procs file, so the ppm scale is unknown.')
  const expRoot = dir.replace(/pdata\/\d+\/$/, '')
  const acqus = files[expRoot + 'acqus'] ? brukerParams(strFromU8(files[expRoot + 'acqus'])) : {}
  const si = Number(procs.SI), sf = Number(procs.SF), offset = Number(procs.OFFSET), swp = Number(procs.SW_P)
  const nc = Number(procs.NC_PROC || 0), big = procs.BYTORDP === '1', dtype = Number(procs.DTYPP || 0)
  const raw = files[path]
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength)
  const width = dtype === 2 ? 8 : 4
  const n = Math.min(si || Infinity, Math.floor(raw.byteLength / width))
  const scale = 2 ** nc
  const ys: number[] = new Array(n)
  for (let i = 0; i < n; i++) ys[i] = (dtype === 2 ? view.getFloat64(i * 8, !big) : view.getInt32(i * 4, !big)) * scale
  const step = swp / sf / n
  const xs = ys.map((_, i) => offset - i * step)
  const nucleus = (acqus.NUC1 || procs.AXNUC || '1H').replace(/^off$/i, '1H')
  const solvent = acqus.SOLVENT || ''
  const title = `${filename.replace(/\.zip$/i, '')} (${expRoot.replace(/\/$/, '') || 'experiment'})`
  return finishPublic({
    title, xLabel: 'δ (ppm)', yLabel: 'Intensity', kind: 'nmr', reversed: true, dips: false,
    nucleus, frequency: sf, solvent, isStick: false,
  }, xs, ys)
}

function finishPublic(meta: Omit<Spectrum, 'x' | 'y'>, xs: number[], ys: number[]): Spectrum {
  const idx = xs.map((_, i) => i)
  if (xs.length > 1 && xs[0] > xs[xs.length - 1]) idx.reverse()
  return { ...meta, x: Float64Array.from(idx.map(i => xs[i])), y: Float64Array.from(idx.map(i => ys[i])) }
}

/** Any supported file, from raw bytes. */
export async function parseSpectrumBuffer(buf: ArrayBuffer, filename: string): Promise<Spectrum> {
  const head = new Uint8Array(buf, 0, Math.min(4, buf.byteLength))
  if (head[0] === 0x50 && head[1] === 0x4b) return parseBrukerZip(buf, filename) // "PK" = zip
  return parseSpectrumFile(new TextDecoder().decode(buf), filename)
}

/** Characterisation label for a spectrum: technique + conditions. */
export function techniqueFor(s: Spectrum): { technique: string; conditions: string } {
  if (s.kind === 'nmr') {
    const nuc = (s.nucleus || '1H').replace(/[^0-9A-Za-z]/g, '')
    const mhz = s.frequency ? `${Math.round(s.frequency)} MHz` : ''
    return { technique: `${nuc} NMR`, conditions: [mhz, s.solvent].filter(Boolean).join(', ') }
  }
  if (s.kind === 'ir') return { technique: 'IR', conditions: '' }
  if (s.kind === 'ms') return { technique: 'MS', conditions: '' }
  if (s.kind === 'uv') return { technique: 'UV-vis', conditions: '' }
  return { technique: s.title || 'Spectrum', conditions: '' }
}

// ---------------------------------------------------------------- automatic analysis

/** Robust noise estimate: median absolute deviation of the point-to-point differences. */
export function noiseLevel(s: Spectrum) {
  const d: number[] = []
  const stride = Math.max(1, Math.floor(s.y.length / 20000))
  for (let i = stride; i < s.y.length; i += stride) d.push(Math.abs(s.y[i] - s.y[i - stride]))
  d.sort((a, b) => a - b)
  return (d[Math.floor(d.length / 2)] || 0) * 1.4826 / Math.SQRT2
}

/** Proton count from a Hill formula such as C13H12O (ignores Hg, Hf, Ho, He…). */
export function hydrogensInFormula(formula?: string | null): number | null {
  if (!formula) return null
  const m = formula.match(/H(\d*)(?![a-z])/)
  return m ? Number(m[1] || 1) : null
}

export interface ImpurityResult {
  short: string; name: string; molPct: number | null; wtPct: number | null
  windows: { lo: number; hi: number }[]; signals: number[] // ppm of each signal, for labels
}
export interface AutoResult {
  regions: { a: number; b: number }[]; refIdx: number; refValue: number; note: string
  impurities: ImpurityResult[]
}

function baseline(s: Spectrum) {
  const sample: number[] = []
  const stride = Math.max(1, Math.floor(s.y.length / 20000))
  for (let i = 0; i < s.y.length; i += stride) sample.push(s.y[i])
  sample.sort((a, b) => a - b)
  return sample[Math.floor(sample.length / 2)] || 0
}

/** Group lines above the noise into signals, skipping anything inside `cuts`. */
function segment(s: Spectrum, cuts: { lo: number; hi: number }[]) {
  const noise = noiseLevel(s), base = baseline(s)
  let max = 0
  for (let i = 0; i < s.y.length; i++) if (s.y[i] > max) max = s.y[i]
  const on = base + Math.max(8 * noise, 0.006 * (max - base))
  const freq = s.frequency || 400
  const gap = 12 / freq // merge lines closer than ~12 Hz into one signal
  const pad = 4 / freq
  const runs: { a: number; b: number }[] = []
  let start = -1
  for (let i = 0; i < s.y.length; i++) {
    const x = s.x[i]
    const above = s.y[i] > on && !cuts.some(c => x >= c.lo && x <= c.hi)
    if (above && start < 0) start = i
    if ((!above || i === s.y.length - 1) && start >= 0) { runs.push({ a: s.x[start], b: s.x[i] }); start = -1 }
  }
  const merged: { a: number; b: number }[] = []
  for (const r of runs) {
    const last = merged[merged.length - 1]
    if (last && r.a - last.b < gap) last.b = r.b
    else merged.push({ ...r })
  }
  let regions = merged.map(r => ({ a: r.a - pad, b: r.b + pad }))
    .filter(r => !cuts.some(c => (r.a + r.b) / 2 >= c.lo - 0.01 && (r.a + r.b) / 2 <= c.hi + 0.01 && r.b - r.a < 0.03))
  let areas = regions.map(r => integrate(s, r.a, r.b))
  const big = Math.max(...areas, 0)
  const keep = areas.map(a => a > 0.04 * big) // drops 13C satellites and baseline wiggles
  regions = regions.filter((_, i) => keep[i]); areas = areas.filter((_, i) => keep[i])
  return { regions, areas }
}

/** Find ¹H signals and set integrals to whole protons.
 *  - Deuterated-solvent residual, water and TMS lines are cut out where they actually are (`solventShift`).
 *  - Residual lab solvents (EtOAc, Et₂O, hexane…) are recognised, kept out of the integrals and quantified,
 *    but only when every one of their signals is present and they are small next to the product, so a
 *    product's own ethyl ester or methyl singlet is never mistaken for a solvent.
 *  - With `expectedH` (protons in the product formula) the integrals are scaled to add up to it. */
export function autoAnalyse1H(s: Spectrum, expectedH: number | null, solventShift = 0, productMW: number | null = null): AutoResult {
  const freq = s.frequency || 400
  const point = (p: number) => ({ lo: p - 3.5 / freq, hi: p + 3.5 / freq })
  const fixed = [...solventMarkers(s).map(m => m.ppm), 0].map(p => point(p + solventShift))
  const cands = findImpurityCandidates(s, solventShift)
    .filter(c => c.def.signals.filter(g => g.mult !== 'm').length <= c.windows.length || c.windows.length === c.def.signals.length)

  const scale = (areas: number[]) => {
    const total = areas.reduce((p, q) => p + q, 0)
    return expectedH && expectedH > 0 ? total / expectedH : Math.min(...areas)
  }
  // measure a little wider than the cut window so the Lorentzian tails are counted
  const perH = (c: FoundImpurity) => Math.min(...c.windows.map(w => integrate(s, w.lo - 4 / freq, w.hi + 4 / freq) / w.nH))

  // pass 1: assume every candidate is an impurity
  let { regions, areas } = segment(s, [...fixed, ...cands.flatMap(c => c.windows)])
  if (!regions.length) return { regions: [], refIdx: 0, refValue: 1, note: 'No signals stood out from the noise.', impurities: [] }
  let unit = scale(areas)
  // pass 2: keep only candidates that are small next to the product. A product's own singlet can mimic
  // a one-line solvent with more protons (a 3H CH3 at 2.14 looks like 50 mol% of 6H acetone), so lone
  // singlets must be under 20 mol%; multi-signal matches, already much more specific, under 50 mol%.
  const confirmed = cands.filter(c => perH(c) < (c.confident ? 0.5 : 0.2) * unit)
  if (confirmed.length !== cands.length) {
    ({ regions, areas } = segment(s, [...fixed, ...confirmed.flatMap(c => c.windows)]))
    unit = scale(areas)
  }

  const impurities: ImpurityResult[] = confirmed.map(c => {
    const mol = 100 * perH(c) / unit
    return {
      short: c.def.short, name: c.def.name,
      molPct: Math.round(mol * 10) / 10,
      wtPct: c.def.mw && productMW ? Math.round(mol * c.def.mw / productMW * 10) / 10 : null,
      windows: c.windows.map(w => ({ lo: w.lo, hi: w.hi })),
      signals: c.windows.map(w => (w.lo + w.hi) / 2),
    }
  })

  let note = expectedH && expectedH > 0
    ? `Scaled so the integrals add up to ${expectedH} H, the product's formula.`
    : 'Scaled so the smallest signal is 1 H (no product formula to check against).'
  // reference = the signal whose value is closest to a whole number
  let refIdx = 0, best = Infinity
  areas.forEach((a, i) => { const v = a / unit, d = Math.abs(v - Math.round(v)) / Math.max(1, v); if (Math.round(v) >= 1 && d < best) { best = d; refIdx = i } })
  const refValue = Math.max(1, Math.round(areas[refIdx] / unit))
  const sum = areas.reduce((p, q) => p + q / areas[refIdx] * refValue, 0)
  if (expectedH && Math.abs(sum - expectedH) > 0.6) note += ` They sum to ${sum.toFixed(1)} H: some signals may overlap the solvent or be exchangeable (OH/NH).`
  if (impurities.length) note += ` Left out as residual solvent: ${impurities.map(i => i.short).join(', ')}.`
  return { regions: regions.sort((p, q) => q.a - p.a), refIdx: regions.length - 1 - refIdx, refValue, note, impurities }
}

/** 13C (and other non-1H) peak list above the noise, solvent signals removed. */
export function autoPeaks13C(s: Spectrum, snr = 6) {
  const noise = noiseLevel(s), base = baseline(s)
  let max = -Infinity, min = Infinity
  for (let i = 0; i < s.y.length; i++) { if (s.y[i] > max) max = s.y[i]; if (s.y[i] < min) min = s.y[i] }
  const cutY = base + snr * noise
  const solv = solventMarkers(s).map(m => m.ppm)
  const out: { x: number; y: number }[] = []
  const step = Math.abs(s.x[1] - s.x[0]) || 1e-3
  const w = Math.max(3, Math.round(0.05 / step)) // ±0.05 ppm neighbourhood for prominence
  for (let i = 2; i < s.y.length - 2; i++) {
    const v = s.y[i]
    if (v > cutY && v >= s.y[i - 1] && v > s.y[i + 1] && v >= s.y[i - 2] && v > s.y[i + 2] && !solv.some(c => Math.abs(s.x[i] - c) < 0.6)) {
      let lowL = v, lowR = v
      for (let k = Math.max(0, i - w); k < i; k++) lowL = Math.min(lowL, s.y[k])
      for (let k = i + 1; k <= Math.min(s.y.length - 1, i + w); k++) lowR = Math.min(lowR, s.y[k])
      if (v - Math.max(lowL, lowR) < snr * noise) continue // a shoulder on a bigger peak, not a peak
      if (!out.some(q => Math.abs(q.x - s.x[i]) < 0.05)) out.push({ x: s.x[i], y: v })
    }
  }
  // the viewer's threshold is a fraction of the visible range; express the same cut that way
  return { peaks: out, threshold: Math.min(0.5, Math.max(0.002, (cutY - min) / ((max - min) || 1))) }
}

/** Where the solvent residual actually is, if it's close to where it should be. */
export function findResidual(s: Spectrum): { label: string; expected: number; found: number } | null {
  const m = solventMarkers(s)[0]
  if (!m) return null
  const win = /13C/i.test(s.nucleus || '') ? 1.0 : 0.15
  const i0 = lowerBound(s.x, m.ppm - win), i1 = Math.min(lowerBound(s.x, m.ppm + win), s.x.length - 1)
  if (i1 - i0 < 3) return null
  let best = -1
  for (let i = i0 + 1; i < i1; i++) {
    if (s.y[i] > s.y[i - 1] && s.y[i] >= s.y[i + 1] && (best < 0 || Math.abs(s.x[i] - m.ppm) < Math.abs(s.x[best] - m.ppm))) {
      // only consider real peaks (above 10x noise)
      if (s.y[i] > 10 * noiseLevel(s)) best = i
    }
  }
  return best < 0 ? null : { label: m.label, expected: m.ppm, found: s.x[best] }
}

// ---------------------------------------------------------------- residual solvents & common impurities
// ¹H shifts from Fulmer et al., Organometallics 2010, 29, 2176 (values vary ±0.02–0.05 ppm with concentration)

interface ImpSignal { ppm: number; mult: 's' | 'd' | 't' | 'q' | 'sept' | 'm'; nH: number }
export interface ImpurityDef { name: string; short: string; mw: number | null; signals: ImpSignal[] }

const S = (ppm: number, nH: number, mult: ImpSignal['mult'] = 's'): ImpSignal => ({ ppm, mult, nH })
const IMP: Record<string, ImpurityDef[]> = {
  CDCL3: [
    { name: 'ethyl acetate', short: 'EtOAc', mw: 88.11, signals: [S(2.05, 3), S(4.12, 2, 'q'), S(1.26, 3, 't')] },
    { name: 'diethyl ether', short: 'Et₂O', mw: 74.12, signals: [S(3.48, 4, 'q'), S(1.21, 6, 't')] },
    { name: 'tetrahydrofuran', short: 'THF', mw: 72.11, signals: [S(3.76, 4, 'm'), S(1.85, 4, 'm')] },
    { name: 'triethylamine', short: 'Et₃N', mw: 101.19, signals: [S(2.53, 6, 'q'), S(1.03, 9, 't')] },
    { name: 'ethanol', short: 'EtOH', mw: 46.07, signals: [S(3.72, 2, 'q'), S(1.25, 3, 't')] },
    { name: '2-propanol', short: 'iPrOH', mw: 60.10, signals: [S(4.04, 1, 'sept'), S(1.22, 6, 'd')] },
    { name: 'tert-butyl methyl ether', short: 'MTBE', mw: 88.15, signals: [S(3.22, 3), S(1.19, 9)] },
    { name: 'N,N-dimethylformamide', short: 'DMF', mw: 73.09, signals: [S(8.02, 1), S(2.96, 3), S(2.88, 3)] },
    { name: 'toluene', short: 'toluene', mw: 92.14, signals: [S(2.36, 3), S(7.17, 3, 'm'), S(7.25, 2, 'm')] },
    { name: 'hexane', short: 'hexane', mw: 86.18, signals: [S(0.88, 6, 't'), S(1.26, 8, 'm')] },
    { name: 'acetone', short: 'acetone', mw: 58.08, signals: [S(2.17, 6)] },
    { name: 'dichloromethane', short: 'CH₂Cl₂', mw: 84.93, signals: [S(5.30, 2)] },
    { name: 'methanol', short: 'MeOH', mw: 32.04, signals: [S(3.49, 3)] },
    { name: 'acetonitrile', short: 'MeCN', mw: 41.05, signals: [S(2.10, 3)] },
    { name: 'dimethyl sulfoxide', short: 'DMSO', mw: 78.13, signals: [S(2.62, 6)] },
    { name: '1,4-dioxane', short: 'dioxane', mw: 88.11, signals: [S(3.71, 8)] },
    { name: 'cyclohexane', short: 'cyclohexane', mw: 84.16, signals: [S(1.43, 12)] },
    { name: 'benzene', short: 'benzene', mw: 78.11, signals: [S(7.36, 6)] },
    { name: 'silicone grease', short: 'grease (Si)', mw: null, signals: [S(0.07, 6)] },
  ],
  DMSO: [
    { name: 'ethyl acetate', short: 'EtOAc', mw: 88.11, signals: [S(1.99, 3), S(4.03, 2, 'q'), S(1.17, 3, 't')] },
    { name: 'diethyl ether', short: 'Et₂O', mw: 74.12, signals: [S(3.38, 4, 'q'), S(1.09, 6, 't')] },
    { name: 'tetrahydrofuran', short: 'THF', mw: 72.11, signals: [S(3.60, 4, 'm'), S(1.76, 4, 'm')] },
    { name: 'triethylamine', short: 'Et₃N', mw: 101.19, signals: [S(2.43, 6, 'q'), S(0.93, 9, 't')] },
    { name: 'ethanol', short: 'EtOH', mw: 46.07, signals: [S(3.44, 2, 'q'), S(1.06, 3, 't')] },
    { name: '2-propanol', short: 'iPrOH', mw: 60.10, signals: [S(3.78, 1, 'sept'), S(1.04, 6, 'd')] },
    { name: 'N,N-dimethylformamide', short: 'DMF', mw: 73.09, signals: [S(7.95, 1), S(2.89, 3), S(2.73, 3)] },
    { name: 'hexane', short: 'hexane', mw: 86.18, signals: [S(0.86, 6, 't'), S(1.25, 8, 'm')] },
    { name: 'toluene', short: 'toluene', mw: 92.14, signals: [S(2.30, 3), S(7.18, 3, 'm'), S(7.25, 2, 'm')] },
    { name: 'acetone', short: 'acetone', mw: 58.08, signals: [S(2.09, 6)] },
    { name: 'dichloromethane', short: 'CH₂Cl₂', mw: 84.93, signals: [S(5.76, 2)] },
    { name: 'methanol', short: 'MeOH', mw: 32.04, signals: [S(3.16, 3)] },
    { name: 'acetonitrile', short: 'MeCN', mw: 41.05, signals: [S(2.07, 3)] },
    { name: 'chloroform', short: 'CHCl₃', mw: 119.38, signals: [S(8.32, 1)] },
    { name: '1,4-dioxane', short: 'dioxane', mw: 88.11, signals: [S(3.57, 8)] },
  ],
  CD3OD: [
    { name: 'ethyl acetate', short: 'EtOAc', mw: 88.11, signals: [S(2.01, 3), S(4.09, 2, 'q'), S(1.24, 3, 't')] },
    { name: 'diethyl ether', short: 'Et₂O', mw: 74.12, signals: [S(3.49, 4, 'q'), S(1.18, 6, 't')] },
    { name: 'tetrahydrofuran', short: 'THF', mw: 72.11, signals: [S(3.71, 4, 'm'), S(1.87, 4, 'm')] },
    { name: 'triethylamine', short: 'Et₃N', mw: 101.19, signals: [S(2.58, 6, 'q'), S(1.05, 9, 't')] },
    { name: 'ethanol', short: 'EtOH', mw: 46.07, signals: [S(3.60, 2, 'q'), S(1.19, 3, 't')] },
    { name: '2-propanol', short: 'iPrOH', mw: 60.10, signals: [S(3.92, 1, 'sept'), S(1.15, 6, 'd')] },
    { name: 'N,N-dimethylformamide', short: 'DMF', mw: 73.09, signals: [S(7.97, 1), S(2.99, 3), S(2.86, 3)] },
    { name: 'hexane', short: 'hexane', mw: 86.18, signals: [S(0.90, 6, 't'), S(1.29, 8, 'm')] },
    { name: 'toluene', short: 'toluene', mw: 92.14, signals: [S(2.32, 3), S(7.16, 3, 'm'), S(7.23, 2, 'm')] },
    { name: 'acetone', short: 'acetone', mw: 58.08, signals: [S(2.15, 6)] },
    { name: 'dichloromethane', short: 'CH₂Cl₂', mw: 84.93, signals: [S(5.49, 2)] },
    { name: 'acetonitrile', short: 'MeCN', mw: 41.05, signals: [S(2.03, 3)] },
    { name: 'chloroform', short: 'CHCl₃', mw: 119.38, signals: [S(7.90, 1)] },
    { name: '1,4-dioxane', short: 'dioxane', mw: 88.11, signals: [S(3.66, 8)] },
  ],
}

export function impurityTable(s: Spectrum): ImpurityDef[] {
  if (s.kind !== 'nmr' || /13C/i.test(s.nucleus || '')) return []
  return IMP[solventKey(s.solvent)] || []
}

const LINES: Record<ImpSignal['mult'], number> = { s: 1, d: 2, t: 3, q: 4, sept: 7, m: 1 }

/** Half-width (ppm) of a solvent signal's window, assuming J ≈ 7 Hz. */
function halfWidth(sig: ImpSignal, freq: number) {
  if (sig.mult === 'm') return 0.06
  return ((LINES[sig.mult] - 1) * 7 / 2 + 3.5) / freq
}

export interface FoundImpurity {
  def: ImpurityDef
  windows: { lo: number; hi: number; nH: number }[] // where its signals actually are
  matched: number                                  // how many of its signals were seen
  confident: boolean                               // multi-signal match (vs a lone singlet)
}

/** Candidate impurities: signals present (±0.04 ppm of the table, after `shift`). */
export function findImpurityCandidates(s: Spectrum, shift = 0): FoundImpurity[] {
  const table = impurityTable(s)
  if (!table.length) return []
  const freq = s.frequency || 400
  const noise = noiseLevel(s), base = baseline(s)
  const out: FoundImpurity[] = []
  for (const def of table) {
    const windows: FoundImpurity['windows'] = []
    for (const sig of def.signals) {
      const centre = sig.ppm + shift
      const i0 = lowerBound(s.x, centre - 0.035), i1 = Math.min(lowerBound(s.x, centre + 0.035), s.x.length - 1)
      let best = -1
      for (let i = Math.max(1, i0); i < i1; i++) {
        if (s.y[i] > base + 10 * noise && s.y[i] >= s.y[i - 1] && s.y[i] > s.y[i + 1] && (best < 0 || s.y[i] > s.y[best])) best = i
      }
      if (best < 0) continue
      // must be a real peak top, not a point on the flank of a bigger neighbour
      const near = (hz: number) => { const j0 = lowerBound(s.x, s.x[best] - hz / freq), j1 = Math.min(lowerBound(s.x, s.x[best] + hz / freq), s.x.length - 1); let m = -Infinity; for (let j = j0; j <= j1; j++) m = Math.max(m, s.y[j]); return m }
      if (near(4) > s.y[best] * 1.001) continue
      const hw = halfWidth(sig, freq)
      if (def.signals.length === 1) {
        // a lone solvent singlet stands apart: the baseline returns on both sides within ~10 Hz
        const quiet = (from: number, to: number) => { const j0 = lowerBound(s.x, from), j1 = Math.min(lowerBound(s.x, to), s.x.length - 1); let m = -Infinity; for (let j = j0; j <= j1; j++) m = Math.max(m, s.y[j]); return m < base + 0.2 * (s.y[best] - base) + 6 * noise }
        if (!quiet(s.x[best] - 14 / freq, s.x[best] - 6 / freq) || !quiet(s.x[best] + 6 / freq, s.x[best] + 14 / freq)) continue
      }
      // for a multiplet, the tallest line may be off-centre; re-centre on the lines found nearby
      let lo = s.x[best] - hw, hi = s.x[best] + hw
      if (LINES[sig.mult] > 1) {
        const lines = pickPeaks(s, lo - hw, hi + hw, 0.15, 12).filter(p => p.y > base + 6 * noise)
        if (lines.length < 2) continue // a lone line where a triplet/quartet should be: not this solvent
        const xs = lines.map(p => p.x).sort((a, b) => a - b)
        const c = (xs[0] + xs[xs.length - 1]) / 2
        lo = c - hw; hi = c + hw
      }
      windows.push({ lo, hi, nH: sig.nH })
    }
    const need = def.signals.length === 1 ? 1 : 2
    if (windows.length >= need) out.push({ def, windows, matched: windows.length, confident: def.signals.length > 1 })
  }
  return out
}
