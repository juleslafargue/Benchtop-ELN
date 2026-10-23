/** Which NMR solvent a spectrum was run in, from the name the instrument wrote (Bruker's SOLVENT, Agilent's solvent,
 * a JCAMP-DX file's .SOLVENT NAME). One key per solvent, used to look up its residual and water signals. */
export function solventKey(solvent?: string): string {
  const s = (solvent || '').toUpperCase().replace(/[\s_]/g, '')
  if (!s) return ''
  if (s.startsWith('DMSO')) return 'DMSO'
  if (s.startsWith('ACETONE')) return 'ACETONE'
  if (s.includes('CHLOROFORM')) return 'CDCL3'
  if (s.includes('METHANOL') || s === 'MEOD' || s.startsWith('MEOD-')) return 'CD3OD'  // TopSpin writes methanol-d4 as "MeOD"
  if (s.includes('BENZENE')) return 'C6D6'
  if (s.includes('ACETONITRILE')) return 'CD3CN'
  if (s.includes('DICHLOROMETHANE') || s.includes('METHYLENECHLORIDE')) return 'CD2CL2'
  if (s.includes('DEUTERIUMOXIDE') || s === 'H2O+D2O' || s === 'D2O+H2O') return 'D2O'
  return s.replace(/-D\d+$/, '')
}
