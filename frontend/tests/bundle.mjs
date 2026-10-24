// spectra.ts imports its neighbours without file extensions, as Vite wants: bundle it with esbuild for node's tests
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

export async function loadSpectra() {
  const { build } = await import('esbuild')
  const dir = mkdtempSync(join(tmpdir(), 'benchtop-spectra-'))
  const out = join(dir, 'spectra.mjs')
  await build({ stdin: { contents: "export * from './spectra'; export * as fid from './fid'", resolveDir: new URL('../src/', import.meta.url).pathname, loader: 'ts' },
    bundle: true, platform: 'node', format: 'esm', outfile: out, logLevel: 'error' })
  const mod = await import(pathToFileURL(out).href)
  rmSync(dir, { recursive: true, force: true })
  return mod
}
