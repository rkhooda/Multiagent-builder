import { useState } from 'react'
import DiffView from './gates/DiffView'

const STAGES = [
  ['research', 'Research'],
  ['requirements', 'Requirements'],
  ['architecture', 'Architecture'],
  ['planning', 'Planning'],
  ['frontend_code', 'Frontend'],
  ['backend_code', 'Backend'],
  ['database', 'Database'],
  ['qa', 'QA'],
  ['devops', 'DevOps'],
]

// Fallback done-detection for projects created before stage_history existed.
const STAGE_DONE_FALLBACK = {
  research: (s) => Boolean(s?.research_report),
  requirements: (s) => Boolean(s?.requirements_doc),
  architecture: (s) => Boolean(s?.architecture_doc),
  planning: (s) => Boolean(s?.implementation_plan),
  frontend_code: () => false,
  backend_code: () => false,
  database: (s) => Object.keys(s?.generated_files || {}).length > 0,
  qa: (s) => Boolean(s?.qa_report),
  devops: (s) => Object.keys(s?.devops_files || {}).length > 0,
}

// Stage -> document field whose previous_versions snapshot powers the diff.
const STAGE_DOC = {
  research: 'research_report',
  requirements: 'requirements_doc',
  architecture: 'architecture_doc',
  planning: 'implementation_plan',
}

const TRIGGER_LABEL = {
  initial: 'initial run',
  edit: 'requested changes',
  back: 'back-navigation',
  skipped: 'skipped after failure',
  restart: 'restarted from this stage',
  partial: 'initial run (some files failed)',
}
const GATE_LABEL = {
  human_gate_1: 'Gate 1',
  human_gate_2: 'Gate 2',
  human_gate_3: 'Gate 3',
  human_gate_4: 'Gate 4',
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

function formatDuration(ms) {
  if (!ms || ms < 0) return ''
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

function attemptLine(entry) {
  const trigger = TRIGGER_LABEL[entry.trigger] || entry.trigger
  const origin = entry.gate_origin ? ` from ${GATE_LABEL[entry.gate_origin] || entry.gate_origin}` : ''
  // durationMs is derived from the preceding history entry, so it is only
  // present once a project has more than one recorded stage.
  const took = entry.durationMs ? ` · took ${formatDuration(entry.durationMs)}` : ''
  return `Attempt ${entry.attempt} — ${trigger}${origin}, ${formatTime(entry.timestamp)}${took}`
}

/**
 * stage_history records only a completion timestamp per stage, so a stage's
 * duration is the gap since the previous entry completed. Derived here rather
 * than persisted — it costs one pass and keeps the state contract unchanged.
 */
function withDurations(history) {
  return history.map((entry, i) => {
    if (i === 0) return entry
    const previous = new Date(history[i - 1].timestamp).getTime()
    const current = new Date(entry.timestamp).getTime()
    if (!previous || !current) return entry
    return { ...entry, durationMs: current - previous }
  })
}

/**
 * Horizontal breadcrumb of pipeline stages with per-stage state, attempt
 * counts, and a hover popover of attempt history. Clicking a completed stage
 * with a snapshot opens the diff for that document.
 */
export default function StageTimeline({ projectState, status, events, regenerating, cycleInfo }) {
  const [diffStage, setDiffStage] = useState(null)

  const history = withDurations(projectState?.stage_history || [])
  const hasHistory = history.length > 0
  const byStage = {}
  history.forEach((entry) => {
    byStage[entry.stage] = [...(byStage[entry.stage] || []), entry]
  })

  // Which stage is executing right now?
  let activeStage = null
  const isRunning = regenerating || status === 'running'
  if (isRunning) {
    if (regenerating && cycleInfo) {
      // Feedback cycle: walk forward from the cycle's target stage, skipping
      // stages whose fresh agent_complete already arrived since the resume.
      const doneSince = new Set(
        (events || [])
          .filter((e) => e.type === 'agent_complete' && new Date(e.timestamp).getTime() > cycleInfo.startedAt)
          .map((e) => e.agent)
      )
      const startIdx = STAGES.findIndex(([key]) => key === cycleInfo.target)
      activeStage = (STAGES.slice(Math.max(startIdx, 0)).find(([key]) => !doneSince.has(key)) || [])[0]
    } else {
      activeStage = (STAGES.find(([key]) => !(byStage[key] || []).length) || [])[0]
    }
  }

  const isErrorStatus = status === 'error_paused' || status === 'rate_limited'

  const stageState = (key) => {
    if (isErrorStatus && projectState?.failed_agent === key) return 'error'
    const attempts = byStage[key] || []
    if (attempts.length && attempts[attempts.length - 1].trigger === 'skipped') return 'skipped'
    if (key === activeStage) return attempts.length ? 'regenerating' : 'active'
    const done = hasHistory ? attempts.length > 0 : STAGE_DONE_FALLBACK[key](projectState)
    return done ? 'done' : 'pending'
  }

  const docField = STAGE_DOC[diffStage]
  const diffOld = docField ? projectState?.previous_versions?.[docField] : ''
  const diffNew = docField ? projectState?.[docField] : ''

  const handleClick = (key) => {
    const field = STAGE_DOC[key]
    if (!field || !projectState?.previous_versions?.[field] || stageState(key) === 'pending') return
    setDiffStage((prev) => (prev === key ? null : key))
  }

  return (
    <div className="border-b border-gray-200 bg-white px-6 py-2">
      <div className="flex items-center gap-1 overflow-x-auto whitespace-nowrap">
        {STAGES.map(([key, label], i) => {
          const state = stageState(key)
          const attempts = byStage[key] || []
          const attemptCount = attempts.length
          const hasDiff = Boolean(STAGE_DOC[key] && projectState?.previous_versions?.[STAGE_DOC[key]])
          return (
            <div key={key} className="flex items-center">
              {i > 0 && <span className="mx-1 text-gray-300">→</span>}
              <div className="group relative">
                <button
                  type="button"
                  onClick={() => handleClick(key)}
                  className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold transition-colors ${
                    state === 'done'
                      ? `text-gray-600 ${hasDiff ? 'hover:bg-blue-50 cursor-pointer' : 'cursor-default'}`
                      : state === 'pending'
                        ? 'cursor-default text-gray-300'
                        : state === 'error'
                          ? 'bg-red-50 text-red-700'
                          : state === 'skipped'
                            ? 'cursor-default text-gray-400'
                            : 'bg-blue-50 text-blue-700'
                  } ${diffStage === key ? 'ring-1 ring-blue-400' : ''}`}
                >
                  {state === 'done' && <span className="text-green-500">✓</span>}
                  {state === 'error' && <span className="h-2 w-2 rounded-full bg-red-500" />}
                  {state === 'skipped' && <span className="text-gray-400">⊘</span>}
                  {state === 'active' && <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />}
                  {state === 'regenerating' && (
                    <span className="h-3 w-3 animate-spin rounded-full border-[1.5px] border-blue-300 border-t-blue-600" />
                  )}
                  {label}
                  {attemptCount > 1 && (
                    <span className="rounded-full bg-amber-100 px-1.5 text-[10px] font-bold text-amber-700">
                      {attemptCount}×
                    </span>
                  )}
                  {(() => {
                    const partial = attempts.reduce((n, e) => n + (e.failed_files?.length || 0), 0)
                    return partial > 0 ? (
                      <span
                        title={`${partial} file(s) failed or blocked`}
                        className="rounded-full bg-amber-100 px-1.5 text-[10px] font-bold text-amber-700"
                      >
                        ⚠ {partial}
                      </span>
                    ) : null
                  })()}
                </button>

                {(attemptCount > 0 || !hasHistory) && (
                  <div className="pointer-events-none absolute left-0 top-full z-30 hidden min-w-[16rem] rounded border border-gray-200 bg-white p-2 shadow-lg group-hover:block">
                    {attemptCount > 0 ? (
                      <>
                        {attempts.map((entry) => (
                          <p key={entry.attempt} className="py-0.5 text-[11px] text-gray-600">
                            {attemptLine(entry)}
                          </p>
                        ))}
                        {hasDiff && state === 'done' && (
                          <p className="pt-1 text-[10px] font-medium text-blue-500">Click to view changes</p>
                        )}
                      </>
                    ) : (
                      <p className="text-[11px] italic text-gray-400">Attempt history unavailable for this project.</p>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {diffStage && diffOld && (
        <div className="mt-2 rounded border border-gray-200">
          <div className="flex items-center justify-between border-b border-gray-100 px-3 py-1.5">
            <span className="text-xs font-semibold text-gray-600">
              {(STAGES.find(([key]) => key === diffStage) || [])[1]} — changes vs previous version
            </span>
            <button
              type="button"
              onClick={() => setDiffStage(null)}
              className="text-xs font-semibold text-gray-400 hover:text-gray-600"
            >
              Close
            </button>
          </div>
          <div className="max-h-80 overflow-auto p-3">
            <DiffView oldText={diffOld} newText={diffNew} />
          </div>
        </div>
      )}
    </div>
  )
}
