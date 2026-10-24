// The ¹H analysis: multiplet labels and couplings, where the solvent residual is, and which lab solvents are
// recognised. Real spectra are in tests/data/nmr (see its README); multiplets are simulated with known answers.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { before, test } from 'node:test'
import { gunzipSync } from 'node:zlib'
import { loadSpectra } from './bundle.mjs'

let sp
before(async () => { sp = await loadSpectra() })

const open = name => {
  let b = readFileSync(new URL(`./data/nmr/${name}`, import.meta.url))
  if (name.endsWith('.gz')) b = gunzipSync(b)
  return sp.parseSpectrumBuffer(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), name.replace(/\.gz$/, ''))
}
/** what the viewer does when a spectrum opens with no product drawn */
const analyse = s => {
  const res = sp.findResidual(s)
  const shift = res && Math.abs(res.found - res.expected) > 0.005 ? res.found - res.expected : 0
  const r = sp.autoAnalyse1H(s, null, shift)
  return { res, impurities: r.impurities.map(i => i.short), signals: r.regions.map(g => sp.analyzeMultiplet(s, g.a, g.b)) }
}
const near = (signals, ppm, tol = 0.03) => signals.find(m => Math.abs(m.shift - ppm) < tol || (m.mult === 'm' && m.lo - tol < ppm && m.hi + tol > ppm))
const list = signals => signals.map(m => `${m.shift.toFixed(2)} ${m.mult}`).join('; ')

// ---------------------------------------------------------------- multiplets

/** lines of a first-order multiplet from its couplings (Hz), e.g. [7, 7, 7] = quartet */
const split = js => js.reduce((lines, j) => lines.flatMap(([o, h]) => [[o - j / 2, h / 2], [o + j / 2, h / 2]]), [[0, 1]])
function multiplet(freq, js, { lw = 0.8, roof = 0, noise = 0.0005 } = {}) {
  const centre = 3, span = 1, n = Math.round(freq * span / 0.12)
  const x = new Float64Array(n), y = new Float64Array(n)
  for (let i = 0; i < n; i++) x[i] = centre - span / 2 + span * i / (n - 1)
  const merged = new Map()
  for (const [o, h] of split(js)) { const k = o.toFixed(3); merged.set(k, (merged.get(k) || 0) + h) }
  const w = lw / freq / 2
  for (const [o, h] of merged) {
    const c = centre + +o / freq, hh = h * (1 + roof * +o / 20)
    for (let i = 0; i < n; i++) { const d = x[i] - c; y[i] += hh * w * w / (d * d + w * w) }
  }
  let seed = 5
  for (let i = 0; i < n; i++) { seed = (seed * 16807) % 2147483647; y[i] += noise * (seed / 2147483647 - 0.5) }
  return { x, y, kind: 'nmr', frequency: freq, isStick: false, dips: false, nucleus: '1H' }
}
const label = (s, freq) => sp.analyzeMultiplet(s, 3 - 40 / freq, 3 + 40 / freq)

test('sextets and septets (regression: an isopropyl CH septet was a "p", a sextet a "dd" or "q": their weak outer lines were missed)', () => {
  for (const freq of [300, 400, 600]) for (const opts of [{}, { lw: 1.5 }, { roof: 0.3 }]) {
    for (const [want, j] of [['sext', 7], ['hept', 6.9], ['sext', 7.4], ['hept', 6.5]]) {
      const m = label(multiplet(freq, Array(want === 'sext' ? 5 : 6).fill(j), opts), freq)
      assert.equal(m.mult, want, `${want} ${j} Hz at ${freq} MHz ${JSON.stringify(opts)}: ${m.mult} ${m.j}`)
      assert.ok(Math.abs(m.j[0] - j) <= 0.3 && Math.abs(m.shift - 3) < 0.002, `${want}: J ${m.j}, at ${m.shift}`)
    }
  }
})

test('ddd and dq (regression: both came out as "m")', () => {
  for (const freq of [300, 400, 600]) for (const opts of [{}, { roof: 0.3 }]) {
    for (const [want, js, expect] of [['ddd', [10, 7, 2], [10, 7, 2]], ['ddd', [8, 6, 4], [8, 6, 4]], ['ddd', [12, 5, 3], [12, 5, 3]],
      ['dq', [7, 1.5, 1.5, 1.5], [7, 1.5]], ['dq', [10, 2, 2, 2], [10, 2]]]) {
      const m = label(multiplet(freq, js, opts), freq)
      assert.equal(m.mult, want, `${want} ${js} at ${freq} MHz ${JSON.stringify(opts)}: ${m.mult} ${m.j}`)
      assert.ok(expect.every((j, k) => Math.abs(m.j[k] - j) <= 0.3), `${want} ${js}: J ${m.j}`)
    }
  }
})

test('the patterns that were already right stay right, and two unrelated doublets are not a sextet', () => {
  for (const freq of [300, 400, 600]) for (const opts of [{}, { lw: 1.5 }, { roof: 0.3 }]) {
    for (const [want, js] of [['s', []], ['d', [8]], ['t', [7, 7]], ['q', [7, 7, 7]], ['p', [7, 7, 7, 7]], ['dd', [12, 4]], ['dt', [15, 6.5, 6.5]], ['td', [7, 7, 2]]]) {
      const m = label(multiplet(freq, js, opts), freq)
      assert.equal(m.mult, want, `${want} ${js} at ${freq} MHz ${JSON.stringify(opts)}: ${m.mult} ${m.j}`)
    }
  }
  const s = multiplet(400, [])
  s.y.fill(0)
  const w = 0.4 / 400
  for (const [hz, h] of [[-12, 0.25], [-4, 0.25], [3, 0.2], [11, 0.2], [20, 0.05], [28, 0.05]]) for (let i = 0; i < s.x.length; i++) { const d = s.x[i] - (3 + hz / 400); s.y[i] += h * w * w / (d * d + w * w) }
  assert.equal(label(s, 400).mult, 'm')
})

// ---------------------------------------------------------------- real spectra, as they open with no product drawn

test('ibuprofen, 600 MHz raw FID (regression: one line of its own doublet was taken for CHCl₃, and its aromatic doublets for toluene, so they were left out)', async () => {
  const { res, impurities, signals } = analyse(await open('ibuprofen_1h_bruker_fid.zip'))
  assert.ok(Math.abs(res.found - 7.286) < 0.003, `CHCl₃ is the lone line at 7.286, found ${res.found}`)
  for (const [ppm, mult, j] of [[7.25, 'd', 8.0], [7.13, 'd', 8.0], [2.47, 'd', 7.2], [1.52, 'd', 7.2], [0.92, 'd', 6.6]]) {
    const m = near(signals, ppm, 0.01)
    assert.ok(m && m.mult === mult && Math.abs(m.j[0] - j) < 0.4, `${ppm} ${mult} ${j} Hz: ${list(signals)}`)
  }
  for (const not of ['toluene', 'Et₃N']) assert.ok(!impurities.includes(not), `${not} (a ¹³C satellite isn't a solvent): ${impurities}`)
})

test('aspirin, 300 MHz (regression: its 7.30 signal was taken for toluene and left out)', async () => {
  const { impurities, signals } = analyse(await open('aspirin_1h_bruker_fid.zip'))
  assert.ok(!impurities.includes('toluene'), `${impurities}`)
  for (const ppm of [8.04, 7.53, 7.29, 7.07, 2.29]) assert.ok(near(signals, ppm), `${ppm}: ${list(signals)}`)
})

test('indometacin, 400 MHz in DMSO (regression: its OCH₃ and CH₂ singlets were taken for MeCN and dioxane)', async () => {
  const { impurities, signals } = analyse(await open('indometacin_1h.dx.gz'))
  assert.deepEqual(impurities, [])
  for (const ppm of [12.35, 7.04, 6.92, 6.72, 3.76, 3.66, 2.22]) assert.ok(near(signals, ppm), `${ppm}: ${list(signals)}`)
})

test('CHCl₃ on top of one of the compound\'s lines is still found there (the nearest line is kept when no lone line is close by)', () => {
  const freq = 400, n = 131072, x = new Float64Array(n), y = new Float64Array(n)
  for (let i = 0; i < n; i++) x[i] = -1 + 13 * i / (n - 1)
  const lor = (c, h, wHz = 1.2) => { const w = wHz / freq / 2; for (let i = 0; i < n; i++) { const d = x[i] - c; y[i] += h * w * w / (d * d + w * w) } }
  lor(7.26, 6); lor(7.26 - 0.002, 10); lor(7.26 - 0.002 - 8 / freq, 10)  // CHCl₃ and a doublet sharing its line
  lor(7.30, 8); lor(7.30 + 8 / freq, 8); lor(3.8, 30)
  const s = { title: 'sim', x, y, xLabel: 'ppm', yLabel: '', kind: 'nmr', reversed: true, dips: false, nucleus: '1H', frequency: freq, solvent: 'CDCl3', isStick: false }
  assert.ok(Math.abs(sp.findResidual(s).found - 7.26) < 0.004, `${sp.findResidual(s).found}`)
})

test('integrals with no formula: a tiny impurity singlet isn\'t "1 H" (regression: ibuprofen came out as 72 H, its CH₃ doublet as 25 H)', async () => {
  const s = await open('ibuprofen_1h_bruker_fid.zip')
  const res = sp.findResidual(s)
  const r = sp.autoAnalyse1H(s, null, res.found - res.expected)
  const unit = sp.integrate(s, r.regions[r.refIdx].a, r.regions[r.refIdx].b) / r.refValue
  const h = r.regions.map(g => ({ at: (g.a + g.b) / 2, n: sp.integrate(s, g.a, g.b) / unit }))
  for (const [ppm, n] of [[7.25, 2], [7.13, 2], [3.73, 1], [2.47, 2], [1.87, 1], [1.52, 3], [0.92, 6]]) {
    const g = h.find(x => Math.abs(x.at - ppm) < 0.05)
    assert.ok(g && Math.abs(g.n - n) < 0.15, `${ppm}: ${n} H, got ${h.map(x => `${x.at.toFixed(2)} ${x.n.toFixed(2)}`).join('; ')}`)
  }
  assert.match(r.note, /apart from a few much smaller signals/)
  // the usual case is unchanged: no outliers, the smallest signal is 1 H
  assert.equal(sp.protonUnit([1, 2, 2, 3]), 1)
  assert.equal(sp.protonUnit([1.02, 3.1, 8.9]), 1.02)  // N-methyl pivalamide: NH, NCH₃, tBu
  assert.equal(sp.protonUnit([0.3, 2, 2, 1, 3, 6]), 1)  // 0.3 left out, the 1 H signal kept
})

test('a CH split by eight protons is an "m", not a quintet (regression: ibuprofen\'s 1.87 was a "p": its region\'s edges left only the five middle lines above the threshold)', async () => {
  for (const freq of [400, 600]) {
    const s = multiplet(freq, Array(8).fill(6.7))
    const half = 3.4 * 6.7 / freq  // a region whose edges sit just inside the outermost lines, as auto-analysis draws them
    const m = sp.analyzeMultiplet(s, 3 - half, 3 + half)
    assert.equal(m.mult, 'm', `${freq} MHz: ${m.mult} ${m.j}`)
    assert.ok(m.hi - m.lo > 5 * 6.7 / freq, `reported as a range: ${m.lo}–${m.hi}`)
  }
  const s = await open('ibuprofen_1h_bruker_fid.zip')
  const res = sp.findResidual(s)
  const r = sp.autoAnalyse1H(s, 18, res.found - res.expected, 206.28, [7.13, 7.12, 3.71, 2.46, 1.86, 1.51, 0.87])  // as the viewer passes the prediction
  const ch = r.regions.map(g => sp.analyzeMultiplet(s, g.a, g.b)).find(m => Math.abs(m.shift - 1.87) < 0.02)
  assert.equal(ch?.mult, 'm', list(r.regions.map(g => sp.analyzeMultiplet(s, g.a, g.b))))
})
