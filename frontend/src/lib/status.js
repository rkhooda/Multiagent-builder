/**
 * Frontend mirror of backend/app/models/status.py.
 *
 * HomePage and ProjectDetailPage each had their own badge-colour switch and they
 * had already drifted (one knew `complete`, the other `done`). One map, both
 * pages — a status added on the backend now has exactly one place to land here.
 */

export const STATUS = {
  running: { label: 'Running', badge: 'bg-blue-100 text-blue-800 border-blue-200', dot: 'bg-blue-500' },
  awaiting_approval: { label: 'Awaiting approval', badge: 'bg-orange-100 text-orange-800 border-orange-200', dot: 'bg-orange-500' },
  rate_limited: { label: 'Rate limited', badge: 'bg-amber-100 text-amber-800 border-amber-200', dot: 'bg-amber-500' },
  error_paused: { label: 'Error', badge: 'bg-red-100 text-red-800 border-red-200', dot: 'bg-red-500' },
  completed: { label: 'Complete', badge: 'bg-green-100 text-green-800 border-green-200', dot: 'bg-green-500' },
  cancelled: { label: 'Cancelled', badge: 'bg-gray-200 text-gray-700 border-gray-300', dot: 'bg-gray-400' },
  // Derived, never persisted: a `running` row that no backend task is driving.
  interrupted: { label: 'Interrupted', badge: 'bg-purple-100 text-purple-800 border-purple-200', dot: 'bg-purple-500' },
}

const FALLBACK = { label: 'Unknown', badge: 'bg-gray-100 text-gray-800 border-gray-200', dot: 'bg-gray-400' }

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
