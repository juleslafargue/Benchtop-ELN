// ketcher-core calls require("raphael") at module load, which doesn't exist in an ES-module
// browser bundle. Provide a tiny global require that hands back the bundled Raphael.
import Raphael from 'raphael'

const g = globalThis as any
if (typeof g.require !== 'function') {
  g.require = (name: string) => {
    if (name === 'raphael') return Raphael
    throw new Error(`Module ${name} is not available in the browser`)
  }
}
if (typeof g.global === 'undefined') g.global = globalThis
if (typeof g.process === 'undefined') g.process = { env: {} }
