import assert from 'node:assert/strict'
import { test } from 'node:test'
import { solventKey } from '../src/solvents.ts'

test('TopSpin\'s "MeOD" is methanol-d4 (regression: its solvent and water peaks were taken for the compound)', () => {
  for (const name of ['MeOD', 'MEOD', 'meod', 'CD3OD', 'cd3od', 'Methanol-d4', 'METHANOL-D4'])
    assert.equal(solventKey(name), 'CD3OD', name)
})

test('the usual ways instruments and JCAMP files name the other solvents', () => {
  const cases = {
    CDCL3: ['CDCl3', 'cdcl3', 'CHLOROFORM-D', 'Chloroform-d'],
    DMSO: ['DMSO', 'dmso', 'DMSO-d6', 'DMSO-D6'],
    ACETONE: ['Acetone', 'ACETONE-D6', 'acetone'],
    C6D6: ['C6D6', 'Benzene-d6'],
    CD3CN: ['CD3CN', 'Acetonitrile-d3', 'ACETONITRILE-D3'],
    CD2CL2: ['CD2Cl2', 'Dichloromethane-d2', 'METHYLENE CHLORIDE-D2'],
    D2O: ['D2O', 'd2o', 'Deuterium oxide', 'H2O+D2O'],
  }
  for (const [key, names] of Object.entries(cases))
    for (const name of names) assert.equal(solventKey(name), key, name)
})

test('nothing, or a solvent without a table, stays as it is', () => {
  assert.equal(solventKey(''), '')
  assert.equal(solventKey(undefined), '')
  assert.equal(solventKey('Tol'), 'TOL')
})
