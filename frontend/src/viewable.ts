/** Which attached files the spectrum viewer opens. No imports, so `npm test` can check it directly. */

export const SPECTRUM_EXT = /\.(jdx|dx|jcamp|jcm|csv|tsv|txt|xy|zip)$/i
/** Raw vendor data zipped by instrument folders (LC-MS .D, .raw, Sciex .wiff, Agilent .fid): not spectra it can read. */
const VENDOR_ZIP = /\.(d|raw|wiff2?|fid)\.zip$/i
/** Opening means downloading the whole file into the browser: above this, offer Download only. */
export const VIEW_MAX_BYTES = 100_000_000

export function canViewSpectrum(a: { filename: string; size?: number }): boolean {
  return SPECTRUM_EXT.test(a.filename) && !VENDOR_ZIP.test(a.filename) && (a.size ?? 0) <= VIEW_MAX_BYTES
}
