// The ¹H auto-analysis on simulated spectra: solvent and water lines are cut where they actually are, whatever the
// field, and the compound's own signals are kept. (spectra.ts imports its neighbours without file extensions, as
// Vite wants, so it is bundled with esbuild first.)
import assert from 'node:assert/strict'
import { before, test } from 'node:test'
import { loadSpectra } from './bundle.mjs'

let sp
before(async () => { sp = await loadSpectra() })

/** A 1D ¹H spectrum: Lorentzian lines on a noisy baseline. lines: [ppm, height, J (Hz), pattern, width (Hz)] */
function spectrum({ freq, solvent, lines, noise = 0.02, n = 131072, lo = -1, hi = 12 }) {
  const x = new Float64Array(n), y = new Float64Array(n)
  for (let i = 0; i < n; i++) x[i] = lo + (hi - lo) * i / (n - 1)
  const lor = (c, h, wHz) => { const w = wHz / freq / 2; for (let i = 0; i < n; i++) { const d = x[i] - c; y[i] += h * w * w / (d * d + w * w) } }
  for (const [c, h, j = 0, pat = [1], w = 1.2] of lines) pat.forEach((r, k) => lor(c + (k - (pat.length - 1) / 2) * j / freq, h * r, w))
  let seed = 7
  for (let i = 0; i < n; i++) { seed = (seed * 16807) % 2147483647; y[i] += noise * (seed / 2147483647 - 0.5) }
  return { title: 'sim', x, y, xLabel: 'ppm', yLabel: '', kind: 'nmr', reversed: true, dips: false, nucleus: '1H', frequency: freq, solvent, isStick: false }
}

const analyse = s => {
  const res = sp.findResidual(s)
  const shift = res && Math.abs(res.found - res.expected) > 0.005 ? res.found - res.expected : 0
  return { res, centres: sp.autoAnalyse1H(s, null, shift).regions.map(r => (r.a + r.b) / 2) }
}
const near = (centres, ppm, tol = 0.03) => centres.some(c => Math.abs(c - ppm) < tol)

// the compound in all three: a few small signals
const compound = [[0.93, 6, 7.2, [1, 2, 1]], [2.45, 4, 7, [1, 2, 1]], [3.62, 8, 0, [1], 1.5], [6.95, 5, 8.4, [1, 1]], [7.21, 5, 8.4, [1, 1]]]

test('900 MHz, MeOD (regression: CHD2OD and water were listed as the compound\'s signals, and noise on the residual\'s tail was taken for it)', () => {
  const s = spectrum({ freq: 900, solvent: 'MeOD', lines: [[4.88, 150, 0, [1], 3], [3.33, 60, 1.6, [1, 2, 3, 2, 1]], ...compound] })
  const { res, centres } = analyse(s)
  assert.ok(res && Math.abs(res.found - 3.33) < 0.002, `residual found at ${res?.found}`)
  for (const solvent of [4.88, 3.33]) assert.ok(!near(centres, solvent, 0.08), `${solvent} is solvent, not a signal: ${centres.map(c => c.toFixed(2))}`)
  for (const own of [7.21, 6.95, 3.62, 2.45]) assert.ok(near(centres, own), `${own} is the compound's: ${centres.map(c => c.toFixed(2))}`)
})

test('400 MHz, DMSO-d6: the water line off its usual place and the DMSO-d5 quintet are cut (regression: water at 3.33 was a signal)', () => {
  const s = spectrum({ freq: 400, solvent: 'DMSO-d6', lines: [[3.36, 40, 0, [1], 6], [2.50, 30, 1.9, [1, 2, 3, 2, 1]], [10.2, 6, 0, [1], 3], [8.0, 8, 8.4, [1, 1]], [7.5, 8, 8.4, [1, 1]], [3.85, 12]] })
  const { res, centres } = analyse(s)
  assert.ok(res && Math.abs(res.found - 2.50) < 0.003, `residual found at ${res?.found}`)
  for (const solvent of [3.36, 2.50]) assert.ok(!near(centres, solvent, 0.06), `${solvent}: ${centres.map(c => c.toFixed(2))}`)
  for (const own of [10.2, 8.0, 7.5, 3.85]) assert.ok(near(centres, own), `${own}: ${centres.map(c => c.toFixed(2))}`)
})

test('400 MHz, CDCl3: a small CHCl3 next to bigger aromatic lines is still found where it is, and the aromatics stay', () => {
  const s = spectrum({ freq: 400, solvent: 'CDCl3', lines: [[7.26, 4, 0, [1], 1], [7.33, 10, 7.5, [1, 2, 1]], [7.40, 10, 7.5, [1, 2, 1]], [3.80, 20], [1.56, 3, 0, [1], 4]] })
  const { res, centres } = analyse(s)
  assert.ok(res && Math.abs(res.found - 7.26) < 0.003, `residual found at ${res?.found}`)
  assert.ok(near(centres, 7.36, 0.06) && near(centres, 3.80), centres.map(c => c.toFixed(2)).join(' '))
  assert.ok(!near(centres, 1.56, 0.03), `water: ${centres.map(c => c.toFixed(2))}`)
})
