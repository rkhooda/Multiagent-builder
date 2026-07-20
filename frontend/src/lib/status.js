/**
 * Frontend mirror of backend/app/models/status.py.
 *
 * HomePage and ProjectDetailPage each had their own badge-colour switch and they
 * had already drifted (one knew `complete`, the other `done`). One map, both
 * pages — a status added on the backend now has exactly one place to land here.
 */

/**
 * Each status carries a design-system `tone`, not class names. Tone drives
 * <Badge tone> and <Dot tone>, so re-colouring a status is a one-word change
 * here and the theme decides what the word looks like.
 *
 * `awaiting_approval` is the only status that gets `accent`, because the accent
 * means exactly one thing across this app: the line has stopped and it needs a
 * human. `rate_limited` deliberately takes the muted `warn` tone rather than
 * the accent — waiting on a provider is not waiting on you, and letting both
 * wear the same gold would spend the signal that makes gates findable.
 */
export const STATUS = {
  running: { label: 'Running', tone: 'run' },
  awaiting_approval: { label: 'Needs you', tone: 'accent' },
  rate_limited: { label: 'Rate limited', tone: 'warn' },
  error_paused: { label: 'Error', tone: 'err' },
  completed: { label: 'Complete', tone: 'ok' },
  cancelled: { label: 'Cancelled', tone: 'idle' },
  // Derived, never persisted: a `running` row that no backend task is driving.
  interrupted: { label: 'Interrupted', tone: 'alt' },
}

const FALLBACK = { label: 'Unknown', tone: 'idle' }

// Transient WebSocket-only states map onto the persisted vocabulary.
const ALIASES = {
  done: 'completed', complete: 'completed', error: 'error_paused',
  // WebSocket connection lifecycle values that reach the badge as-is.
  connecting: 'running', reconnecting: 'running', connected: 'running',
}

export function statusKey(status, interrupted = false) {
  if (interrupted) return 'interrupted'
  const key = ALIASES[status] || status
  return STATUS[key] ? key : 'unknown'
}

export function statusMeta(status, interrupted = false) {
  return STATUS[statusKey(status, interrupted)] || FALLBACK
}

export const STAGE_LABELS = {
  research: 'Research',
  requirements: 'Requirements',
  architecture: 'Architecture',
  planning: 'Planning',
  frontend_code: 'Frontend Code',
  backend_code: 'Backend Code',
  database: 'Database',
  validation: 'Validation',
  qa: 'QA',
  devops: 'DevOps',
  code: 'Code Generation',
  cancelled: 'Cancelled',
  completed: 'Complete',
  human_gate_1: 'Gate 1 — Requirements',
  human_gate_2: 'Gate 2 — Architecture',
  human_gate_3: 'Gate 3 — Plan',
  human_gate_4: 'Gate 4 — Final Review',
}

export function stageLabel(stage) {
  if (!stage) return '—'
  return STAGE_LABELS[stage] || stage.replace(/_/g, ' ')
}

/** Stages a restart can target, in pipeline order. */
export const RESTART_STAGES = [
  ['research', 'Research'],
  ['requirements', 'Requirements'],
  ['architecture', 'Architecture'],
  ['planning', 'Planning'],
  ['code_generation', 'Code Generation'],
]

export function formatDate(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return String(iso)
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatRelative(iso) {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const minutes = Math.round((Date.now() - then) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}
