/**
 * Raw Bruker FID -> spectrum, in the browser.
 *
 * Steps (checked against Bruker's own processing of the same ibuprofen data: correlation 0.99 for 1H):
 *  1. read fid (int32 or float64, either byte order), interleaved real/imaginary
 *  2. exponential apodisation (line broadening LB)
 *  3. zero-fill to twice the next power of two
 *  4. FFT, centre the zero frequency
 *  5. remove the digital filter's group delay (a linear phase of GRPDLY points)
 *  6. reverse so index 0 is the highest frequency (Bruker convention)
 *  7. automatic phasing (ACME: minimise derivative entropy, penalise negative intensity) - adjustable by hand
 *  8. polynomial baseline through the signal-free points
 * ppm axis: (O1 + offset from carrier) / BF1, which reproduces TopSpin's OFFSET/SW_p scale.
 */

export interface FidData {
  re: Float64Array          // complex spectrum after FFT + group-delay removal, ascending ppm order
  im: Float64Array
  x: Float64Array           // ppm, ascending
  ph0: number               // degrees, pivot at the centre
  ph1: number
  auto: { ph0: number; ph1: number }
  lb: number
  points: number            // complex points acquired
  zeroFilled: number
  groupDelay: number
}

type Params = Record<string, string>

// Bruker's digital filter delay, in points, for firmware that doesn't write GRPDLY (DSPFVS 10-13), by DECIM
// (the values TopSpin uses; also in nmrglue's bruker module)
const DSP: Record<number, Record<number, number>> = {
  10: { 2: 44.75, 3: 33.5, 4: 66.625, 6: 59.083333333333333, 8: 68.5625, 12: 60.375, 16: 69.53125, 24: 61.020833333333333, 32: 70.015625, 48: 61.34375, 64: 70.2578125, 96: 61.505208333333333, 128: 70.37890625, 192: 61.5859375, 256: 70.439453125, 384: 61.626302083333333, 512: 70.4697265625, 768: 61.646484375, 1024: 70.48486328125, 1536: 61.656575520833333, 2048: 70.492431640625 },
  11: { 2: 46, 3: 36.5, 4: 48, 6: 50.166666666666667, 8: 53.25, 12: 69.5, 16: 72.25, 24: 70.166666666666667, 32: 72.75, 48: 70.5, 64: 73, 96: 70.666666666666667, 128: 72.5, 192: 71.333333333333333, 256: 72.25, 384: 71.666666666666667, 512: 72.125, 768: 71.833333333333333, 1024: 72.0625, 1536: 71.916666666666667, 2048: 72.03125 },
  12: { 2: 46, 3: 36.5, 4: 48, 6: 50.166666666666667, 8: 53.25, 12: 69.5, 16: 71.625, 24: 70.166666666666667, 32: 72.125, 48: 70.5, 64: 72.375, 96: 70.666666666666667, 128: 72.5, 192: 71.333333333333333, 256: 72.25, 384: 71.666666666666667, 512: 72.125, 768: 71.833333333333333, 1024: 72.0625, 1536: 71.916666666666667, 2048: 72.03125 },
  13: { 2: 2.75, 3: 2.8333333333333333, 4: 2.875, 6: 2.9166666666666667, 8: 2.9375, 12: 2.9583333333333333, 16: 2.96875, 24: 2.9791666666666667, 32: 2.984375, 48: 2.9895833333333333, 64: 2.9921875, 96: 2.9947916666666667 },
}

/** The digital filter's delay of a Bruker FID: GRPDLY when TopSpin wrote it, else from the firmware table;
 * undefined when neither says (the FID's own shape is then used). Keys may carry a "$" (JCAMP-DX) or not (acqus). */
export function brukerGroupDelay(p: Params): number | undefined {
  const get = (k: string) => Number(p[k] ?? p['$' + k])
  const g = get('GRPDLY')
  if (g >= 0) return g
  const t = DSP[Math.round(get('DSPFVS'))]
  const d = t?.[Math.round(get('DECIM'))]
  return d
}

/** In-place iterative radix-2 FFT (n must be a power of two). */
function fft(re: Float64Array, im: Float64Array) {
  const n = re.length
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1
    for (; j & bit; bit >>= 1) j ^= bit
    j ^= bit
    if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = -2 * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang)
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0
      for (let k = 0; k < len / 2; k++) {
        const a = i + k, b = a + len / 2
        const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr
        re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti
        const nr = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = nr
      }
    }
  }
}

/** Phase the complex spectrum: real part after rotating by ph0 + ph1 * (i/N - 1/2) degrees. */
export function phased(f: Pick<FidData, 're' | 'im'>, ph0: number, ph1: number, out?: Float64Array): Float64Array {
  const n = f.re.length, y = out || new Float64Array(n)
  const d2r = Math.PI / 180
  for (let i = 0; i < n; i++) {
    // our arrays are in ascending-ppm order, so position across the spectrum runs from the right edge
    const k = (n - 1 - i) / n - 0.5
    const a = (ph0 + ph1 * k) * d2r
    y[i] = f.re[i] * Math.cos(a) - f.im[i] * Math.sin(a)
  }
  return y
}

/** ACME (Chen et al. 2002): entropy of the first derivative, plus a penalty for negative intensity. */
function acme(re: Float64Array, im: Float64Array, ph0: number, ph1: number): number {
  const y = phased({ re, im }, ph0, ph1)
  let mx = 0
  for (let i = 0; i < y.length; i++) mx = Math.max(mx, Math.abs(y[i]))
  if (!mx) return Infinity
  let sum = 0
  const ds = new Float64Array(y.length - 2)
  for (let i = 0; i < ds.length; i++) { ds[i] = Math.abs(y[i + 2] - y[i]) / (2 * mx); sum += ds[i] }
  let h = 0
  for (let i = 0; i < ds.length; i++) { const p = ds[i] / sum; if (p > 0) h -= p * Math.log(p) }
  let neg = 0
  for (let i = 0; i < y.length; i++) if (y[i] < 0) neg += (y[i] / mx) ** 2
  return h + 1e5 * neg / y.length
}


function nelderMead(f: (a: number, b: number) => number, x0: [number, number], step: [number, number], iters: number): [number, number] {
  let pts: [number, number][] = [x0, [x0[0] + step[0], x0[1]], [x0[0], x0[1] + step[1]]]
  let vals = pts.map(p => f(p[0], p[1]))
  for (let it = 0; it < iters; it++) {
    const o = [0, 1, 2].sort((a, b) => vals[a] - vals[b]); pts = o.map(i => pts[i]); vals = o.map(i => vals[i])
    const c: [number, number] = [(pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2]
    const xr: [number, number] = [2 * c[0] - pts[2][0], 2 * c[1] - pts[2][1]], fr = f(...xr)
    if (fr < vals[0]) {
      const xe: [number, number] = [3 * c[0] - 2 * pts[2][0], 3 * c[1] - 2 * pts[2][1]], fe = f(...xe)
      if (fe < fr) { pts[2] = xe; vals[2] = fe } else { pts[2] = xr; vals[2] = fr }
    } else if (fr < vals[1]) { pts[2] = xr; vals[2] = fr }
    else {
      const xc: [number, number] = [(c[0] + pts[2][0]) / 2, (c[1] + pts[2][1]) / 2], fc = f(...xc)
      if (fc < vals[2]) { pts[2] = xc; vals[2] = fc }
      else { for (const i of [1, 2]) { pts[i] = [(pts[0][0] + pts[i][0]) / 2, (pts[0][1] + pts[i][1]) / 2]; vals[i] = f(...pts[i]) } }
    }
  }
  return pts[vals.indexOf(Math.min(...vals))]
}

export function autoPhase(f: Pick<FidData, 're' | 'im'>): { ph0: number; ph1: number } {
  const dec = (a: Float64Array, k: number) => Float64Array.from({ length: Math.floor(a.length / k) }, (_, i) => a[i * k])
  // a coarse copy (about 8k points) for the wide search, every 4th point for the first fine search, then all of it
  const k1 = Math.max(1, Math.floor(f.re.length / 8192)), k2 = 4
  const cr = dec(f.re, k1), ci = dec(f.im, k1)
  const sr = dec(f.re, k2), si = dec(f.im, k2)
  let best = [Infinity, 0, 0]
  // first-order phase can be large (a time shift not quite removed, other vendors' data): search ±720° first,
  // so the fine search starts near the best answer instead of drifting from a poor one
  for (let p0 = 0; p0 < 360; p0 += 15) for (let p1 = -720; p1 <= 720; p1 += 45) {
    const v = acme(cr, ci, p0, p1)
    if (v < best[0]) best = [v, p0, p1]
  }
  let x = nelderMead((a, b) => acme(sr, si, a, b), [best[1], best[2]], [10, 20], 150)
  x = nelderMead((a, b) => acme(f.re, f.im, a, b), x, [2, 5], 80)
  return { ph0: ((x[0] % 360) + 360) % 360, ph1: x[1] }
}

/** Subtract a 5th-order polynomial fitted to the quietest 60% of points. */
export function baselineCorrect(y: Float64Array): Float64Array {
  const n = y.length, order = 5
  const sorted = Float64Array.from(y).sort()
  const cut = sorted[Math.floor(n * 0.6)]
  // normal equations on a decimated set of quiet points (fast and plenty for a smooth baseline)
  const ata = Array.from({ length: order + 1 }, () => new Array(order + 1).fill(0)), atb = new Array(order + 1).fill(0)
  const stride = Math.max(1, Math.floor(n / 8000))
  for (let i = 0; i < n; i += stride) {
    if (y[i] > cut) continue
    const t = (2 * i) / (n - 1) - 1
    const pw = [1]; for (let k = 1; k <= order; k++) pw.push(pw[k - 1] * t)
    for (let r = 0; r <= order; r++) { atb[r] += pw[r] * y[i]; for (let c = 0; c <= order; c++) ata[r][c] += pw[r] * pw[c] }
  }
  // Gaussian elimination
  const m = ata.map((row, r) => [...row, atb[r]])
  for (let c = 0; c <= order; c++) {
    let p = c
    for (let r = c + 1; r <= order; r++) if (Math.abs(m[r][c]) > Math.abs(m[p][c])) p = r
    const tmp = m[c]; m[c] = m[p]; m[p] = tmp
    if (Math.abs(m[c][c]) < 1e-12) return y
    for (let r = 0; r <= order; r++) if (r !== c) { const f = m[r][c] / m[c][c]; for (let k = c; k <= order + 1; k++) m[r][k] -= f * m[c][k] }
  }
  const coef = m.map((row, r) => row[order + 1] / row[r])
  const out = new Float64Array(n)
  for (let i = 0; i < n; i++) { const t = (2 * i) / (n - 1) - 1; let b = 0, pw = 1; for (let k = 0; k <= order; k++) { b += coef[k] * pw; pw *= t } out[i] = y[i] - b }
  return out
}

/** Process a Bruker fid with its acqus parameters. `sf` is TopSpin's reference frequency (procs SF) when the zip
 * has it: a spectrum referenced in TopSpin (SR) then keeps that referencing. */
export function processFid(raw: Uint8Array, acqus: Params, opts: { lb?: number; ph0?: number; ph1?: number; sf?: number } = {}): FidData & { y: Float64Array } {
  const big = acqus.BYTORDA === '1', float = acqus.DTYPA === '2'
  const width = float ? 8 : 4
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength)
  const td = Math.min(Number(acqus.TD) || Infinity, Math.floor(raw.byteLength / width))
  const npts = Math.floor(td / 2)
  if (npts < 16) throw new Error('This FID is too short to process.')
  const sw = Number(acqus.SW_H), bf1 = Number(acqus.BF1) || Number(acqus.SFO1), o1 = Number(acqus.O1) || 0
  if (!(sw > 0) || !(bf1 > 0)) throw new Error('The acqus file is missing the spectral width or frequency, so the FID can\'t be processed.')
  const re = new Float64Array(npts), im = new Float64Array(npts)
  for (let i = 0; i < npts; i++) {
    re[i] = float ? view.getFloat64(2 * i * 8, !big) : view.getInt32(2 * i * 4, !big)
    im[i] = float ? view.getFloat64((2 * i + 1) * 8, !big) : view.getInt32((2 * i + 1) * 4, !big)
  }
  const sf = opts.sf && Math.abs(opts.sf - bf1) < 1e-4 * bf1 ? opts.sf : bf1
  // ppm of the spectrum's centre: the carrier (BF1 + O1) measured from the reference frequency SF
  const centre = (o1 + (bf1 - sf) * 1e6) / sf
  return processComplexFid(re, im, { sw, sf, centre, nucleus: acqus.NUC1 || '1H', groupDelay: brukerGroupDelay(acqus) }, opts)
}

/** Any complex FID (Bruker, or a JCAMP-DX FID from a benchtop spectrometer) -> spectrum. sw: spectral width (Hz),
 * sf: reference frequency (MHz), centre: ppm at the middle of the spectrum. groupDelay: the digital filter's delay in
 * points if known; otherwise taken from where the FID peaks. */
export function processComplexFid(fre: ArrayLike<number>, fim: ArrayLike<number>,
  p: { sw: number; sf: number; centre: number; nucleus: string; groupDelay?: number; reverse?: boolean },
  opts: { lb?: number; ph0?: number; ph1?: number } = {}): FidData & { y: Float64Array } {
  const npts = Math.min(fre.length, fim.length)
  if (npts < 16) throw new Error('This FID is too short to process.')
  const { sw, sf, centre } = p
  if (!(sw > 0) || !(sf > 0) || !Number.isFinite(centre)) throw new Error('The spectral width or frequency is missing, so the FID can\'t be processed.')
  const nuc = p.nucleus || '1H'
  const lb = opts.lb ?? (/13C/.test(nuc) ? 1.0 : /19F|31P/.test(nuc) ? 1.0 : 0.3)
  let N = 1; while (N < npts) N <<= 1; N <<= 1
  const re = new Float64Array(N), im = new Float64Array(N)
  const sign = p.reverse ? -1 : 1  // the other quadrature convention: conjugating the FID mirrors the spectrum
  for (let i = 0; i < npts; i++) {
    const w = Math.exp(-Math.PI * lb * i / sw)
    re[i] = fre[i] * w; im[i] = sign * fim[i] * w
  }
  // group delay: from GRPDLY when TopSpin wrote it, otherwise where the FID peaks (the filter's delay shows up there)
  // a negative delay: points missing at the start (a benchtop spectrometer's dead time), the opposite time shift
  let grp = p.groupDelay === undefined || p.groupDelay === null ? NaN : Number(p.groupDelay)
  if (!Number.isFinite(grp)) {
    let mi = 0, mx = 0
    for (let i = 0; i < Math.min(npts, 400); i++) { const m = Math.hypot(re[i], im[i]); if (m > mx) { mx = m; mi = i } }
    grp = mi
  }
  fft(re, im)
  // fftshift + group-delay linear phase + reverse to ascending ppm, in one pass
  const R = new Float64Array(N), I = new Float64Array(N)
  for (let j = 0; j < N; j++) {
    const src = (j + N / 2) % N            // after fftshift, position j holds frequency index src
    const k = j - N / 2
    const a = 2 * Math.PI * grp * k / N
    const c = Math.cos(a), s = Math.sin(a)
    const vr = re[src] * c - im[src] * s, vi = re[src] * s + im[src] * c
    // Bruker: high frequency first; we store ascending ppm, so un-reverse = keep j order (low ppm first)
    R[j] = vr; I[j] = vi
  }
  const x = new Float64Array(N)
  for (let j = 0; j < N; j++) x[j] = centre + (-sw / 2 + (j + 1) * sw / N) / sf
  const auto = autoPhase({ re: R, im: I })
  const ph0 = opts.ph0 ?? auto.ph0, ph1 = opts.ph1 ?? auto.ph1
  const y = baselineCorrect(phased({ re: R, im: I }, ph0, ph1))
  return { re: R, im: I, x, y, ph0, ph1, auto, lb, points: npts, zeroFilled: N, groupDelay: grp }
}
