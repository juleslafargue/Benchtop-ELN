/** Benchtop's mark: a lowercase b whose bowl is a benzene ring, with a stir bar in it.
 *  Ink follows the text colour and the stir bar the accent, so it matches light and dark themes. */
export default function Logo({ size = 24, title }: { size?: number; title?: string }) {
  return (
    <svg className="logo" viewBox="-34 -64 120 132" width={size * 120 / 132} height={size} role={title ? 'img' : undefined} aria-hidden={title ? undefined : true} aria-label={title}>
      <line x1="0" y1="-52" x2="0" y2="36" stroke="currentColor" strokeWidth="10" strokeLinecap="round" />
      <polygon points="0,12 26,-3 52,12 52,42 26,57 0,42" fill="none" stroke="currentColor" strokeWidth="10" strokeLinejoin="round" />
      <rect x="10" y="21.5" width="32" height="11" rx="5.5" transform="rotate(-30 26 27)" fill="var(--accent)" />
    </svg>
  )
}
