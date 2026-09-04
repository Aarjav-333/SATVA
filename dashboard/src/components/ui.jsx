/**
 * Shared presentational components for both dashboards.
 *
 * Two of these carry product rules rather than just styling:
 *
 * - `DemoDataBadge` — the specification is explicit that seeded presentation
 *   data must never be mistaken for collected evidence. Every row that came
 *   from the seeder carries `is_synthetic`, and this badge makes that visible
 *   wherever such a row is rendered.
 * - `SeverityBadge` — severity is shown alongside *why*, because a cluster that
 *   is being withheld for lack of corroboration looks alarming otherwise.
 */

export function Card({ children, className = '' }) {
  return <div className={`card ${className}`}>{children}</div>
}

export function CardHeader({ title, subtitle, actions }) {
  return (
    <div className="flex items-start justify-between gap-4 px-5 pt-5 pb-3">
      <div>
        <h2 className="text-base font-bold tracking-tight">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-ink-soft">{subtitle}</p>}
      </div>
      {actions}
    </div>
  )
}

export function Stat({ label, value, hint, tone = 'ink' }) {
  const tones = {
    ink: 'text-ink',
    brand: 'text-brand',
    caution: 'text-caution',
    confirmed: 'text-confirmed',
    clear: 'text-clear',
  }
  return (
    <div className="card card-pad">
      <p className="label">{label}</p>
      <p className={`stat mt-1.5 ${tones[tone] ?? tones.ink}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-ink-soft leading-snug">{hint}</p>}
    </div>
  )
}

const SEVERITY = {
  high: { cls: 'bg-confirmed-soft text-confirmed', label: 'High' },
  elevated: { cls: 'bg-caution-soft text-caution', label: 'Elevated' },
  advisory: { cls: 'bg-unsure-soft text-unsure', label: 'Advisory' },
}

export function SeverityBadge({ severity }) {
  const entry = SEVERITY[severity] ?? SEVERITY.advisory
  return <span className={`badge ${entry.cls}`}>{entry.label}</span>
}

/**
 * Marks a record that came from the demo seeder.
 *
 * Deliberately prominent. Quietly styling it as a subtle grey chip would defeat
 * the purpose: a judge or an officer must be able to tell at a glance which
 * rows are real.
 */
export function DemoDataBadge({ className = '' }) {
  return (
    <span
      className={`badge bg-neutral2-soft text-neutral2 border border-line ${className}`}
      title="Seeded demonstration data. Not collected evidence."
    >
      Demo data
    </span>
  )
}

export function PublishedBadge({ isPublishable, suppressionReason }) {
  if (isPublishable) {
    return <span className="badge bg-clear-soft text-clear">On public map</span>
  }
  return (
    <span
      className="badge bg-neutral2-soft text-neutral2"
      title={suppressionReason ?? 'Withheld from the public map'}
    >
      Withheld
    </span>
  )
}

export function EmptyState({ icon = '—', title, body, action }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="mb-3 text-2xl text-ink-faint">{icon}</div>
      <p className="font-semibold">{title}</p>
      {body && <p className="mt-1 max-w-md text-sm text-ink-soft">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }) {
  return (
    <div className="flex items-center justify-center gap-3 px-6 py-12 text-sm text-ink-soft">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-brand" />
      {label}
    </div>
  )
}

export function ErrorNotice({ error, onRetry }) {
  return (
    <div className="rounded-xl border border-caution/30 bg-caution-soft px-4 py-3">
      <p className="text-sm font-semibold text-caution">
        {error?.code === 'permission_denied'
          ? 'Your account does not have access to this view'
          : 'Something went wrong'}
      </p>
      <p className="mt-1 text-sm text-ink-soft">{error?.message ?? String(error)}</p>
      {onRetry && (
        <button type="button" className="btn-ghost mt-3" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

/**
 * The standing disclaimer.
 *
 * Rendered on every dashboard surface. The wording matches
 * `app.core.constants.DISCLAIMER_LONG` and the mobile app so it cannot drift
 * between the three places a user might read it.
 */
export function Disclaimer({ compact = false }) {
  if (compact) {
    return (
      <p className="text-xs text-ink-faint">
        SATVA is a screening aid, not a statutory test or certification.
      </p>
    )
  }
  return (
    <div className="rounded-xl border border-line bg-white px-4 py-3">
      <p className="text-xs leading-relaxed text-ink-soft">
        <span className="font-semibold text-ink">SATVA is a screening aid.</span> It is not a
        statutory test, a certification, or a legal determination of food safety. A visual
        screening result is advisory only and must never be treated as proof of adulteration.
        Only a confirmatory colorimetric strip reading may be escalated, and escalation is
        routed to the statutory FSSAI channel rather than replacing it.
      </p>
    </div>
  )
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-soft">{subtitle}</p>}
      </div>
      {children}
    </header>
  )
}
