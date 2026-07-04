import { useMemo, useState } from 'react'

const PHASE_CONFIG = {
  database: { label: 'Database', color: 'bg-purple-500', light: 'bg-purple-50', border: 'border-purple-200', text: 'text-purple-700', dot: 'DB' },
  backend: { label: 'Backend', color: 'bg-blue-500', light: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-700', dot: 'BE' },
  frontend: { label: 'Frontend', color: 'bg-green-500', light: 'bg-green-50', border: 'border-green-200', text: 'text-green-700', dot: 'FE' },
  devops: { label: 'DevOps', color: 'bg-orange-500', light: 'bg-orange-50', border: 'border-orange-200', text: 'text-orange-700', dot: 'DO' },
}

const COMPLEXITY_CONFIG = {
  low: { label: 'Low', color: 'bg-green-100 text-green-700' },
  medium: { label: 'Medium', color: 'bg-yellow-100 text-yellow-700' },
  high: { label: 'High', color: 'bg-red-100 text-red-700' },
}

const COMPLEXITY_MINUTES = { low: 3, medium: 7, high: 14 }

export default function TaskPlanViewer({ planJson, onTasksChanged }) {
  const [selectedPhase, setSelectedPhase] = useState('all')
  const [expandedTask, setExpandedTask] = useState(null)
  const [excludedTasks, setExcludedTasks] = useState(new Set())

  const tasks = useMemo(() => {
    try {
      const parsed = JSON.parse(planJson)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return null
    }
  }, [planJson])

  if (tasks === null) {
    return (
      <div className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-600">
        Could not parse implementation plan. Raw output: {planJson?.slice(0, 200)}
      </div>
    )
  }

  const phases = ['all', 'database', 'backend', 'frontend', 'devops']
  const filteredTasks = selectedPhase === 'all' ? tasks : tasks.filter((task) => task.phase === selectedPhase)
  const activeTasks = tasks.filter((task) => !excludedTasks.has(task.id))

  const phaseCounts = Object.fromEntries(
    ['database', 'backend', 'frontend', 'devops'].map((phase) => [
      phase,
      tasks.filter((task) => task.phase === phase && !excludedTasks.has(task.id)).length,
    ])
  )

  const estimatedMinutes = activeTasks.reduce(
    (sum, task) => sum + (COMPLEXITY_MINUTES[task.estimated_complexity] || 7),
    0
  )

  const executionWaves = activeTasks.reduce((waves, task) => {
    const dependencyDepth = (task.requires || []).length
    const waveIndex = Math.min(dependencyDepth, 5)
    if (!waves[waveIndex]) waves[waveIndex] = []
    waves[waveIndex].push(task.id)
    return waves
  }, [])

  const toggleExclude = (taskId) => {
    setExcludedTasks((prev) => {
      const next = new Set(prev)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }

      if (onTasksChanged) {
        const includedTasks = tasks.filter((task) => !next.has(task.id))
        onTasksChanged(includedTasks)
      }

      return next
    })
  }

  return (
    <div>
      <div className="grid grid-cols-5 gap-2 mb-4">
        <div className="col-span-1 rounded border border-gray-200 bg-gray-50 p-2 text-center">
          <div className="text-xl font-bold text-gray-800">{activeTasks.length}</div>
          <div className="text-xs text-gray-500">Total Files</div>
        </div>
        {['database', 'backend', 'frontend', 'devops'].map((phase) => {
          const cfg = PHASE_CONFIG[phase]
          return (
            <div key={phase} className={`${cfg.light} ${cfg.border} rounded border p-2 text-center`}>
              <div className={`text-lg font-bold ${cfg.text}`}>{phaseCounts[phase]}</div>
              <div className={`text-xs ${cfg.text}`}>{cfg.label}</div>
            </div>
          )
        })}
      </div>

      <div className="mb-4 rounded border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600">
        <div className="flex items-center justify-between gap-3">
          <span>Estimated generation time: <strong>~{Math.round(estimatedMinutes)} minutes</strong></span>
          <span className="text-xs text-gray-400">Based on task complexity</span>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-gray-500">
          {executionWaves.filter(Boolean).map((wave, index) => (
            <span key={`wave-${index}`} className="rounded bg-white px-2 py-1 border border-gray-200">
              Wave {index + 1}: {wave.length} task{wave.length === 1 ? '' : 's'}
            </span>
          ))}
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        {phases.map((phase) => {
          const cfg = phase === 'all' ? null : PHASE_CONFIG[phase]
          const count = phase === 'all' ? activeTasks.length : phaseCounts[phase]
          return (
            <button
              key={phase}
              type="button"
              onClick={() => setSelectedPhase(phase)}
              className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
                selectedPhase === phase
                  ? phase === 'all'
                    ? 'bg-gray-800 text-white'
                    : `${cfg.color} text-white`
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {phase === 'all' ? `All (${count})` : `${cfg.dot} ${cfg.label} (${count})`}
            </button>
          )
        })}
      </div>

      <div className="max-h-80 space-y-1 overflow-y-auto">
        {filteredTasks.map((task) => {
          const cfg = PHASE_CONFIG[task.phase]
          const compCfg = COMPLEXITY_CONFIG[task.estimated_complexity] || COMPLEXITY_CONFIG.medium
          const isExcluded = excludedTasks.has(task.id)
          const isExpanded = expandedTask === task.id
          const widthClass = {
            low: 'w-16',
            medium: 'w-28',
            high: 'w-40',
          }[task.estimated_complexity] || 'w-28'

          return (
            <div key={task.id}>
              <div
                className={`flex items-center gap-2 rounded border p-2 cursor-pointer transition-all ${
                  isExcluded ? 'border-gray-200 bg-gray-50 opacity-50' : `${cfg.light} ${cfg.border}`
                }`}
                onClick={() => setExpandedTask(isExpanded ? null : task.id)}
              >
                <input
                  type="checkbox"
                  checked={!isExcluded}
                  onChange={(event) => {
                    event.stopPropagation()
                    toggleExclude(task.id)
                  }}
                  className="h-3 w-3 cursor-pointer"
                />

                <div className={`h-6 w-1 rounded ${cfg.color} flex-shrink-0`} />

                <span className={`w-12 flex-shrink-0 font-mono text-xs font-bold ${cfg.text}`}>
                  {task.id}
                </span>

                <span className={`min-w-0 flex-1 truncate text-xs font-medium ${isExcluded ? 'text-gray-400 line-through' : 'text-gray-700'}`}>
                  {task.filename}
                </span>

                <div className="hidden sm:flex flex-shrink-0 items-center gap-2">
                  <div className="h-3 w-40 rounded-full bg-white/70 border border-white/60 overflow-hidden">
                    <div className={`${widthClass} h-full ${cfg.color} opacity-80`} />
                  </div>
                </div>

                <span className={`flex-shrink-0 rounded px-2 py-0.5 text-xs ${compCfg.color}`}>
                  {compCfg.label}
                </span>

                <span className="text-xs text-gray-400">{isExpanded ? '▲' : '▼'}</span>
              </div>

              {isExpanded && (
                <div className={`ml-6 rounded-b border-x border-b p-3 text-xs ${cfg.light} ${cfg.border}`}>
                  <p className="mb-2 text-gray-700">{task.description}</p>
                  <div className="flex flex-wrap gap-4 text-gray-500">
                    <span>Path: {task.filepath}</span>
                    {task.requires?.length > 0 && <span>Requires: {task.requires.join(', ')}</span>}
                    {task.context_sections?.length > 0 && <span>Context: {task.context_sections.join(', ')}</span>}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {excludedTasks.size > 0 && (
        <p className="mt-2 text-xs text-orange-600">
          {excludedTasks.size} task(s) unchecked and will be skipped during generation.
        </p>
      )}
    </div>
  )
}
