import { useEffect, useMemo, useRef, useState } from 'react'
import DiffView from './DiffView'
import FeedbackInput from './FeedbackInput'
import RegeneratingOverlay from './RegeneratingOverlay'

const PHASES = ['frontend', 'backend', 'database', 'devops']

const PHASE_CONFIG = {
  frontend: { label: 'Frontend', prefix: 'fe', color: 'bg-green-500', light: 'bg-green-50', border: 'border-green-200', text: 'text-green-700' },
  backend: { label: 'Backend', prefix: 'be', color: 'bg-blue-500', light: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-700' },
  database: { label: 'Database', prefix: 'db', color: 'bg-purple-500', light: 'bg-purple-50', border: 'border-purple-200', text: 'text-purple-700' },
  devops: { label: 'DevOps', prefix: 'dv', color: 'bg-orange-500', light: 'bg-orange-50', border: 'border-orange-200', text: 'text-orange-700' },
}

const COMPLEXITY_CONFIG = {
  low: { label: 'Low', badge: 'bg-green-100 text-green-700' },
  medium: { label: 'Medium', badge: 'bg-amber-100 text-amber-700' },
  high: { label: 'High', badge: 'bg-red-100 text-red-700' },
}

// Deliberately rough 5/10/15-minute heuristic — always labeled "est." in the UI.
export const COMPLEXITY_MINUTES = { low: 5, medium: 10, high: 15 }

function nextTaskId(phase, allTasks) {
  const prefix = PHASE_CONFIG[phase].prefix
  const max = allTasks.reduce((m, t) => {
    const match = /^([a-z]{2})_(\d+)$/.exec(t.id || '')
    return match && match[1] === prefix ? Math.max(m, parseInt(match[2], 10)) : m
  }, 0)
  return `${prefix}_${String(max + 1).padStart(3, '0')}`
}

// Serialize included tasks in canonical phase order (database -> backend ->
// frontend -> devops, matching the planner's contract), preserving relative
// order within each phase so requires-based context injection stays sane.
function serializePlan(tasks, excluded) {
  const included = tasks.filter((t) => !excluded.has(t.id))
  const ordered = ['database', 'backend', 'frontend', 'devops'].flatMap((phase) =>
    included.filter((t) => t.phase === phase)
  )
  // Anything with an unknown phase shouldn't be silently dropped
  const known = new Set(ordered.map((t) => t.id))
  return JSON.stringify([...ordered, ...included.filter((t) => !known.has(t.id))], null, 2)
}

// Nested {name: subtree} map built from filepaths; files hold null.
function buildFileTree(filepaths) {
  const root = {}
  filepaths.forEach((path) => {
    const parts = path.split('/').filter(Boolean)
    let node = root
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1
      if (isFile) {
        node[part] = null
      } else {
        node[part] = node[part] || {}
        node = node[part]
      }
    })
  })
  return root
}

function FileTree({ node, depth = 0 }) {
  // Folders first, then files, each alphabetical
  const entries = Object.entries(node).sort(([an, av], [bn, bv]) => {
    if ((av === null) !== (bv === null)) return av === null ? 1 : -1
    return an.localeCompare(bn)
  })
  return entries.map(([name, child]) => (
    <div key={name} style={{ paddingLeft: depth * 16 }}>
      <span className="font-mono text-xs leading-6 text-gray-700">
        {child === null ? '📄 ' : '📁 '}
        {name}
      </span>
      {child !== null && <FileTree node={child} depth={depth + 1} />}
    </div>
  ))
}

function SummaryBar({ tasks, excluded, extraActions }) {
  const included = tasks.filter((t) => !excluded.has(t.id))
  const phaseCount = (phase) => included.filter((t) => t.phase === phase).length
  const complexityCount = (level) => included.filter((t) => (t.estimated_complexity || 'medium') === level).length
  const totalMinutes = included.reduce(
    (sum, t) => sum + (COMPLEXITY_MINUTES[t.estimated_complexity] || COMPLEXITY_MINUTES.medium),
    0
  )

  return (
    <div className="sticky top-0 z-20 rounded-lg border border-gray-200 bg-white/95 px-4 py-2.5 shadow-sm backdrop-blur-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        <span className="font-semibold text-gray-800">
          {included.length} task{included.length === 1 ? '' : 's'}
        </span>
        <span className="text-gray-500">
          {phaseCount('frontend')} frontend · {phaseCount('backend')} backend · {phaseCount('database')} database ·{' '}
          {phaseCount('devops')} devops
        </span>
        {excluded.size > 0 && (
          <span className="font-medium text-orange-600">{excluded.size} excluded</span>
        )}
        <span className="ml-auto flex items-center gap-3">
          <span className="flex items-center gap-1">
            <span className="rounded bg-green-100 px-1.5 py-0.5 font-semibold text-green-700">{complexityCount('low')} low</span>
            <span className="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-700">{complexityCount('medium')} med</span>
            <span className="rounded bg-red-100 px-1.5 py-0.5 font-semibold text-red-700">{complexityCount('high')} high</span>
          </span>
          <span className="font-semibold text-gray-800" title="Rough heuristic: 5/10/15 min per low/medium/high task">
            ⏱ ~{totalMinutes} min <span className="font-normal text-gray-400">est.</span>
          </span>
          {extraActions}
        </span>
      </div>
    </div>
  )
}

function AddTaskForm({ phase, allTasks, onAdd, onCancel }) {
  const [form, setForm] = useState({ filename: '', filepath: '', description: '', complexity: 'medium', requires: [] })
  const [error, setError] = useState('')

  const submit = () => {
    if (!form.filename.trim() || !form.filepath.trim()) return setError('Filename and filepath are required.')
    if (!form.filepath.split('/').pop().includes('.')) return setError('Filepath must include a file extension.')
    if (form.description.trim().length < 50) {
      return setError(`Description must be at least 50 characters so the coder agent has enough context (currently ${form.description.trim().length}).`)
    }
    onAdd({
      id: nextTaskId(phase, allTasks),
      phase,
      filename: form.filename.trim(),
      filepath: form.filepath.trim(),
      description: form.description.trim(),
      requires: form.requires,
      context_sections: [],
      estimated_complexity: form.complexity,
      custom: true,
    })
  }

  return (
    <div className="mt-2 space-y-2 rounded border border-dashed border-gray-300 bg-white p-3">
      <div className="grid grid-cols-2 gap-2">
        <input
          value={form.filename}
          onChange={(e) => setForm({ ...form, filename: e.target.value })}
          placeholder="Filename (e.g. Footer.jsx)"
          className="rounded border border-gray-300 px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <input
          value={form.filepath}
          onChange={(e) => setForm({ ...form, filepath: e.target.value })}
          placeholder="Filepath (e.g. frontend/src/components/Footer.jsx)"
          className="rounded border border-gray-300 px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>
      <textarea
        value={form.description}
        onChange={(e) => setForm({ ...form, description: e.target.value })}
        placeholder="What must this file contain? Be specific — min 50 characters."
        rows={3}
        className="w-full rounded border border-gray-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
      />
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-gray-600">
          Complexity
          <select
            value={form.complexity}
            onChange={(e) => setForm({ ...form, complexity: e.target.value })}
            className="rounded border border-gray-300 px-1.5 py-1 text-xs"
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-600">
          Depends on
          <select
            multiple
            value={form.requires}
            onChange={(e) => setForm({ ...form, requires: [...e.target.selectedOptions].map((o) => o.value) })}
            className="max-h-16 rounded border border-gray-300 px-1.5 py-1 font-mono text-xs"
          >
            {allTasks.map((t) => (
              <option key={t.id} value={t.id}>{t.id} — {t.filename}</option>
            ))}
          </select>
        </label>
        <div className="ml-auto flex gap-2">
          <button type="button" onClick={submit} className="rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700">
            Add Task
          </button>
          <button type="button" onClick={onCancel} className="rounded border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50">
            Cancel
          </button>
        </div>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  )
}

export default function Gate3Approval({ projectId, projectState, status, onResume, lastAgentComplete }) {
  const planJson = projectState?.implementation_plan || ''

  const [tasks, setTasks] = useState([])
  const [excluded, setExcluded] = useState(new Set())
  const [removed, setRemoved] = useState([])
  const [dirty, setDirty] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [editingText, setEditingText] = useState('')
  const [addingPhase, setAddingPhase] = useState(null)
  const [pendingRemove, setPendingRemove] = useState(null)
  const [highlightId, setHighlightId] = useState(null)
  const [collapsed, setCollapsed] = useState(new Set())
  const [showPreview, setShowPreview] = useState(false)
  const [feedbackMode, setFeedbackMode] = useState(null) // 'edit' | 'back' | null
  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [submitting, setSubmitting] = useState(null)
  const [regenAction, setRegenAction] = useState(null) // 'edit' | 'back' while re-running
  const [patchErrors, setPatchErrors] = useState([])
  const [parseError, setParseError] = useState(false)
  const [showArchBanner, setShowArchBanner] = useState(false)
  const [showArchDiff, setShowArchDiff] = useState(false)
  const hasLeftApprovalRef = useRef(false)

  // When the gate re-fires after a regeneration cycle (edit or back), dismiss
  // the overlay; after a back-navigation, surface the architecture diff banner.
  useEffect(() => {
    if (status !== 'awaiting_approval') {
      hasLeftApprovalRef.current = true
    } else if (hasLeftApprovalRef.current) {
      if (regenAction === 'back') setShowArchBanner(true)
      setRegenAction(null)
      hasLeftApprovalRef.current = false
    }
  }, [status, regenAction])

  // Re-initialize the local editing copy whenever a genuinely new plan arrives
  // (first load, replan, back-navigation) — but never on unrelated metadata refetches.
  const lastPlanRef = useRef(null)
  useEffect(() => {
    if (!planJson || planJson === lastPlanRef.current) return
    lastPlanRef.current = planJson
    try {
      const parsed = JSON.parse(planJson)
      setTasks(Array.isArray(parsed) ? parsed : [])
      setParseError(!Array.isArray(parsed))
    } catch {
      setTasks([])
      setParseError(true)
    }
    setExcluded(new Set())
    setRemoved([])
    setDirty(false)
    setEditingId(null)
    setAddingPhase(null)
    setPendingRemove(null)
    setPatchErrors([])
  }, [planJson])

  const includedTasks = useMemo(() => tasks.filter((t) => !excluded.has(t.id)), [tasks, excluded])
  const includedIds = useMemo(() => new Set(includedTasks.map((t) => t.id)), [includedTasks])

  // Included tasks that depend on a given (excluded/removed) task id
  const includedDependentsOf = (id) => includedTasks.filter((t) => (t.requires || []).includes(id))

  // Ids whose exclusion currently breaks an included task's requires —
  // the plan cannot be approved while this is non-empty (backend 422 is the safety net).
  const brokenDeps = useMemo(() => {
    const broken = new Map() // missing id -> [dependent ids]
    includedTasks.forEach((t) => {
      ;(t.requires || []).forEach((dep) => {
        if (!includedIds.has(dep)) broken.set(dep, [...(broken.get(dep) || []), t.id])
      })
    })
    return broken
  }, [includedTasks, includedIds])

  const planValid = brokenDeps.size === 0 && includedTasks.length > 0

  // Transitive closure of included tasks that (directly or indirectly) require `id`
  const dependentsClosure = (id) => {
    const closure = new Set([id])
    let grew = true
    while (grew) {
      grew = false
      includedTasks.forEach((t) => {
        if (!closure.has(t.id) && (t.requires || []).some((dep) => closure.has(dep))) {
          closure.add(t.id)
          grew = true
        }
      })
    }
    closure.delete(id)
    return closure
  }

  const toggleTask = (id) => {
    setExcluded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
    setDirty(true)
  }

  const excludeDependentsToo = (id) => {
    setExcluded((prev) => new Set([...prev, ...dependentsClosure(id)]))
    setDirty(true)
  }

  const removeTask = (id, force = false) => {
    const dependents = includedDependentsOf(id).filter((t) => t.id !== id)
    if (dependents.length > 0 && !force) {
      setPendingRemove(id)
      return
    }
    if (force) setExcluded((prev) => new Set([...prev, ...dependentsClosure(id)]))
    setTasks((prev) => {
      const target = prev.find((t) => t.id === id)
      if (target) setRemoved((r) => [...r, target])
      return prev.filter((t) => t.id !== id)
    })
    setExcluded((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
    setPendingRemove(null)
    setDirty(true)
  }

  const saveDescription = (id) => {
    setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, description: editingText } : t)))
    setEditingId(null)
    setDirty(true)
  }

  const addTask = (task) => {
    setTasks((prev) => [...prev, task])
    setAddingPhase(null)
    setDirty(true)
  }

  const scrollToTask = (id) => {
    document.getElementById(`task-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setHighlightId(id)
    setTimeout(() => setHighlightId(null), 1600)
  }

  // Persist the edited plan to the paused graph. Returns true on success.
  const patchPlan = async () => {
    const excludedPayload = [...tasks.filter((t) => excluded.has(t.id)), ...removed]
    const res = await fetch(`http://localhost:8000/api/projects/${projectId}/state`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        field: 'implementation_plan',
        content: serializePlan(tasks, excluded),
        excluded_tasks: excludedPayload,
      }),
    })
    if (res.status === 422) {
      const body = await res.json()
      setPatchErrors(body.detail?.errors || ['Plan failed validation'])
      return false
    }
    if (!res.ok) {
      setPatchErrors([`Failed to save plan (HTTP ${res.status})`])
      return false
    }
    setPatchErrors([])
    return true
  }

  const handleApprove = async () => {
    if (!planValid) return
    setSubmitting('approve')
    try {
      if (await patchPlan()) await onResume('approve', '')
    } finally {
      setSubmitting(null)
    }
  }

  const handleFeedbackSubmit = async (feedbackText) => {
    const action = feedbackMode
    setSubmitting(action)
    setRegenAction(action)
    try {
      // Replan: persist edits first (when consistent) so the planner's
      // PREVIOUS OUTPUT reflects what the user actually kept. An inconsistent
      // draft is skipped, not blocked — the replan discards it anyway.
      if (action === 'edit' && dirty && planValid) await patchPlan()
      await onResume(action, feedbackText)
      setFeedbackMode(null)
    } catch {
      setRegenAction(null)
    } finally {
      setSubmitting(null)
    }
  }

  const handleCancelProject = async () => {
    setSubmitting('reject')
    try {
      await onResume('reject', '')
    } finally {
      setSubmitting(null)
      setConfirmingCancel(false)
    }
  }

  const isPending = submitting !== null
  const regenLabel =
    regenAction === 'back'
      ? lastAgentComplete === 'architecture'
        ? 'Replanning with the new architecture…'
        : 'Regenerating architecture…'
      : 'Replanning with your feedback…'

  if (parseError) {
    return (
      <div className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-600">
        Could not parse the implementation plan. Raw output: {planJson.slice(0, 300)}
      </div>
    )
  }

  const renderTaskCard = (task) => {
    const cfg = PHASE_CONFIG[task.phase] || PHASE_CONFIG.backend
    const compCfg = COMPLEXITY_CONFIG[task.estimated_complexity] || COMPLEXITY_CONFIG.medium
    const isExcluded = excluded.has(task.id)
    const dependents = isExcluded ? includedDependentsOf(task.id) : []
    const isBrokenDependent = includedIds.has(task.id) && (task.requires || []).some((d) => !includedIds.has(d))
    const isEditing = editingId === task.id
    const isPendingRemove = pendingRemove === task.id
    const removeDependents = isPendingRemove ? includedDependentsOf(task.id) : []

    return (
      <div
        key={task.id}
        id={`task-${task.id}`}
        className={`rounded border p-3 transition-all ${
          highlightId === task.id
            ? 'border-blue-400 ring-2 ring-blue-300'
            : isBrokenDependent
              ? 'border-red-300 bg-red-50'
              : isExcluded
                ? 'border-gray-200 bg-gray-50 opacity-60'
                : `${cfg.border} bg-white`
        }`}
      >
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={!isExcluded}
            onChange={() => toggleTask(task.id)}
            disabled={isPending}
            className="mt-1 h-4 w-4 cursor-pointer"
            aria-label={`Include ${task.filename}`}
          />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`font-mono text-xs font-bold ${cfg.text}`}>{task.id}</span>
              <span className={`text-sm font-semibold ${isExcluded ? 'text-gray-400 line-through' : 'text-gray-800'}`}>
                {task.filename}
              </span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${compCfg.badge}`}>{compCfg.label}</span>
              {task.custom && (
                <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">Custom</span>
              )}
            </div>
            <p className={`mt-0.5 font-mono text-[11px] ${isExcluded ? 'text-gray-300' : 'text-gray-400'}`}>{task.filepath}</p>

            {isEditing ? (
              <div className="mt-2 space-y-2">
                <textarea
                  value={editingText}
                  onChange={(e) => setEditingText(e.target.value)}
                  rows={3}
                  autoFocus
                  className="w-full rounded border border-blue-300 px-2 py-1.5 text-xs text-gray-800 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
                <div className="flex gap-2">
                  <button type="button" onClick={() => saveDescription(task.id)} className="rounded bg-blue-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-blue-700">
                    Save
                  </button>
                  <button type="button" onClick={() => setEditingId(null)} className="rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-50">
                    Discard
                  </button>
                </div>
              </div>
            ) : (
              <p className={`mt-1.5 text-xs leading-relaxed ${isExcluded ? 'text-gray-400 line-through' : 'text-gray-600'}`}>
                {task.description}
              </p>
            )}

            {(task.requires || []).length > 0 && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Requires</span>
                {task.requires.map((dep) => (
                  <button
                    key={dep}
                    type="button"
                    onClick={() => scrollToTask(dep)}
                    className={`rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold transition-colors ${
                      includedIds.has(dep)
                        ? 'border-gray-300 bg-gray-100 text-gray-600 hover:bg-gray-200'
                        : 'border-red-300 bg-red-100 text-red-700 hover:bg-red-200'
                    }`}
                    title={includedIds.has(dep) ? `Jump to ${dep}` : `${dep} is excluded or removed!`}
                  >
                    {dep}
                  </button>
                ))}
              </div>
            )}

            {isExcluded && dependents.length > 0 && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded border border-amber-300 bg-amber-50 px-2.5 py-1.5">
                <span className="text-xs text-amber-800">
                  ⚠️ {dependents.length} included task{dependents.length === 1 ? '' : 's'} depend{dependents.length === 1 ? 's' : ''} on this:{' '}
                  {dependents.map((d) => d.id).join(', ')}
                </span>
                <button
                  type="button"
                  onClick={() => excludeDependentsToo(task.id)}
                  className="rounded bg-amber-600 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-amber-700"
                >
                  Exclude dependents too
                </button>
              </div>
            )}

            {isPendingRemove && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded border border-red-300 bg-red-50 px-2.5 py-1.5">
                <span className="text-xs text-red-800">
                  {removeDependents.length} included task{removeDependents.length === 1 ? '' : 's'} depend on this:{' '}
                  {removeDependents.map((d) => d.id).join(', ')}
                </span>
                <button
                  type="button"
                  onClick={() => removeTask(task.id, true)}
                  className="rounded bg-red-600 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-red-700"
                >
                  Remove & exclude dependents
                </button>
                <button
                  type="button"
                  onClick={() => setPendingRemove(null)}
                  className="rounded border border-gray-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-gray-600 hover:bg-gray-50"
                >
                  Keep
                </button>
              </div>
            )}
          </div>

          <div className="flex flex-shrink-0 gap-1">
            <button
              type="button"
              onClick={() => {
                setEditingId(task.id)
                setEditingText(task.description)
              }}
              disabled={isPending}
              title="Edit description"
              className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
            >
              ✏️
            </button>
            <button
              type="button"
              onClick={() => removeTask(task.id)}
              disabled={isPending}
              title="Remove task entirely"
              className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
            >
              🗑️
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center space-x-2">
        <span className="h-2.5 w-2.5 rounded-full bg-orange-500 animate-pulse" />
        <h3 className="text-base font-bold text-gray-900 uppercase tracking-wide">
          Gate 3 — Implementation Plan Review
        </h3>
      </div>
      <p className="text-sm text-gray-600">
        Edit the plan below — uncheck tasks to skip them, edit descriptions, add or remove tasks.
        The coder agents will generate exactly what you approve here.
      </p>

      {showArchBanner && (
        <div className="rounded-lg border border-blue-300 bg-blue-50 px-4 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm font-medium text-blue-800">
              🔄 Architecture was regenerated — the plan below was rebuilt from it.
            </span>
            {projectState?.previous_versions?.architecture_doc && (
              <button
                type="button"
                onClick={() => setShowArchDiff((v) => !v)}
                className="rounded border border-blue-300 bg-white px-2.5 py-1 text-xs font-semibold text-blue-700 hover:bg-blue-100"
              >
                {showArchDiff ? 'Hide changes' : 'Review changes'}
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setShowArchBanner(false)
                setShowArchDiff(false)
              }}
              className="ml-auto text-xs font-semibold text-blue-400 hover:text-blue-600"
            >
              Dismiss
            </button>
          </div>
          {showArchDiff && (
            <div className="mt-3">
              <DiffView
                oldText={projectState?.previous_versions?.architecture_doc || ''}
                newText={projectState?.architecture_doc || ''}
              />
            </div>
          )}
        </div>
      )}

      <SummaryBar
        tasks={tasks}
        excluded={excluded}
        extraActions={
          <button
            type="button"
            onClick={() => setShowPreview((v) => !v)}
            className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
          >
            {showPreview ? 'Hide Folder Structure' : '📁 Preview Folder Structure'}
          </button>
        }
      />

      {/* Rebuilt from included filepaths on every render, so it live-updates as tasks are edited */}
      {showPreview && (
        <div className="max-h-80 overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-4">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            {includedTasks.length} file{includedTasks.length === 1 ? '' : 's'} will be generated
          </p>
          {includedTasks.length === 0 ? (
            <p className="text-xs italic text-gray-400">No tasks included.</p>
          ) : (
            <FileTree node={buildFileTree(includedTasks.map((t) => t.filepath))} />
          )}
        </div>
      )}

      <div className="relative">
        {regenAction && <RegeneratingOverlay label={regenLabel} />}

        <div className="space-y-3">
          {PHASES.map((phase) => {
            const cfg = PHASE_CONFIG[phase]
            const phaseTasks = tasks.filter((t) => t.phase === phase)
            const phaseIncluded = phaseTasks.filter((t) => !excluded.has(t.id))
            const isCollapsed = collapsed.has(phase)

            return (
              <div key={phase} className={`rounded-lg border ${cfg.border} overflow-hidden`}>
                <button
                  type="button"
                  onClick={() =>
                    setCollapsed((prev) => {
                      const next = new Set(prev)
                      next.has(phase) ? next.delete(phase) : next.add(phase)
                      return next
                    })
                  }
                  className={`flex w-full items-center justify-between ${cfg.light} px-4 py-2.5 text-left`}
                >
                  <span className={`flex items-center gap-2 text-sm font-bold ${cfg.text}`}>
                    <span className={`h-2 w-2 rounded-full ${cfg.color}`} />
                    {cfg.label}
                    <span className="font-normal">
                      — {phaseIncluded.length}/{phaseTasks.length} task{phaseTasks.length === 1 ? '' : 's'}
                    </span>
                  </span>
                  <span className="text-xs text-gray-400">{isCollapsed ? '▸' : '▾'}</span>
                </button>

                {!isCollapsed && (
                  <div className="space-y-2 p-3">
                    {phaseTasks.length === 0 && (
                      <p className="text-xs italic text-gray-400">No {cfg.label.toLowerCase()} tasks in this plan.</p>
                    )}
                    {phaseTasks.map(renderTaskCard)}
                    {addingPhase === phase ? (
                      <AddTaskForm phase={phase} allTasks={tasks} onAdd={addTask} onCancel={() => setAddingPhase(null)} />
                    ) : (
                      <button
                        type="button"
                        onClick={() => setAddingPhase(phase)}
                        disabled={isPending}
                        className={`w-full rounded border border-dashed ${cfg.border} py-1.5 text-xs font-semibold ${cfg.text} hover:${cfg.light}`}
                      >
                        + Add custom {cfg.label.toLowerCase()} task
                      </button>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {patchErrors.length > 0 && (
        <div className="rounded border border-red-300 bg-red-50 p-3">
          <p className="mb-1 text-sm font-semibold text-red-700">The plan failed validation:</p>
          <ul className="list-inside list-disc space-y-0.5 text-xs text-red-600">
            {patchErrors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      {!planValid && brokenDeps.size > 0 && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          Cannot approve: {brokenDeps.size} excluded/removed task{brokenDeps.size === 1 ? '' : 's'} still required by
          included tasks ({[...brokenDeps.keys()].join(', ')}). Re-check them or exclude their dependents.
        </div>
      )}

      <div className="sticky bottom-0 space-y-3 border-t border-gray-100 bg-white pt-3">
        {confirmingCancel ? (
          <div className="rounded border border-red-200 bg-red-50 p-3 space-y-2">
            <p className="text-sm font-medium text-red-700">Cancel this project? This cannot be undone.</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleCancelProject}
                disabled={isPending}
                className="rounded bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700"
              >
                {submitting === 'reject' ? 'Cancelling...' : 'Yes, cancel project'}
              </button>
              <button
                type="button"
                onClick={() => setConfirmingCancel(false)}
                disabled={isPending}
                className="rounded border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50"
              >
                Keep project
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={handleApprove}
              disabled={isPending || regenAction || !planValid}
              title={planValid ? '' : 'Fix the dependency conflicts first'}
              className="flex-1 rounded bg-green-600 py-2 px-3 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-60"
            >
              {submitting === 'approve' ? 'Saving plan...' : '✅ Approve & Start Coding'}
            </button>
            <button
              type="button"
              onClick={() => setFeedbackMode(feedbackMode === 'edit' ? null : 'edit')}
              disabled={isPending || regenAction}
              className="flex-1 rounded border border-gray-300 bg-white py-2 px-3 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-60"
            >
              🔄 Replan with Feedback
            </button>
            <button
              type="button"
              onClick={() => setFeedbackMode(feedbackMode === 'back' ? null : 'back')}
              disabled={isPending || regenAction}
              className="flex-1 rounded border border-gray-300 bg-white py-2 px-3 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-60"
            >
              ⬅️ Go Back to Architecture
            </button>
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={isPending || regenAction}
              className="rounded border border-red-200 bg-white py-2 px-3 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-60"
            >
              Cancel Project
            </button>
          </div>
        )}

        {feedbackMode && !confirmingCancel && (
          <FeedbackInput
            label={
              feedbackMode === 'back'
                ? 'What should change in the architecture?'
                : 'What should change in the plan?'
            }
            onSubmit={handleFeedbackSubmit}
            onCancel={() => setFeedbackMode(null)}
            submitting={submitting === feedbackMode}
          />
        )}
      </div>
    </div>
  )
}
