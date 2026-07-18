// Per-phase code-generation progress (Day 20). Files generate in parallel and
// out of order, so the bar is driven by the {done, failed, blocked, total} count
// SNAPSHOT the scheduler stamps into every file_started/written/failed/blocked
// event — never incremented locally, so it is idempotent under reconnect/replay.

// Fold the event stream into per-phase progress. In-flight files come from
// file_started events not yet resolved by a written/failed/blocked event.
export function derivePhaseProgress(events) {
  const FILE_EVENTS = ['file_started', 'file_written', 'file_failed', 'file_blocked']
  const order = []
  const phases = {}
  for (const e of events) {
    if (!FILE_EVENTS.includes(e.type)) continue
    const ph = e.phase || 'code'
    if (!phases[ph]) {
      phases[ph] = { phase: ph, done: 0, failed: 0, blocked: 0, total: 0, inflight: new Map() }
      order.push(ph)
    }
    const p = phases[ph]
    if (typeof e.total === 'number') {
      p.done = e.done ?? p.done
      p.failed = e.failed ?? p.failed
      p.blocked = e.blocked ?? p.blocked
      p.total = e.total
    }
    if (e.type === 'file_started') p.inflight.set(e.filepath, e.filename)
    else p.inflight.delete(e.filepath)  // resolved -> no longer in flight
  }
  return order.map((ph) => {
    const p = phases[ph]
    const settled = p.done + p.failed + p.blocked
    return {
      ...p,
      inflight: [...p.inflight.values()],
      settled,
      complete: p.total > 0 && settled >= p.total,
      hasIssues: p.failed + p.blocked > 0,
    }
  })
}

const LABELS = { frontend: 'frontend', backend: 'backend', code: 'code' }

export default function PhaseProgress({ phase }) {
  const { phase: name, done, failed, blocked, total, inflight, complete, hasIssues } = phase
  if (!total) return null
  const width = (n) => `${(n / total) * 100}%`
  const label = LABELS[name] || name

  return (
    <div className="rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-xs">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-gray-700">
          {complete ? 'Generated' : 'Generating'} {label}: {done} of {total} files
        </span>
        <div className="flex items-center gap-2 text-[11px] font-mono">
          {failed > 0 && (
            <span className="text-red-600 bg-red-50 border border-red-200 px-1.5 py-0.5 rounded">
              {failed} failed
            </span>
          )}
          {blocked > 0 && (
            <span className="text-gray-600 bg-gray-100 border border-gray-300 px-1.5 py-0.5 rounded">
              {blocked} blocked
            </span>
          )}
        </div>
      </div>

      {/* Segmented bar: green done, red failed, grey blocked, over a light track. */}
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
        <div className="bg-green-500 transition-all duration-300" style={{ width: width(done) }} />
        <div className="bg-red-500 transition-all duration-300" style={{ width: width(failed) }} />
        <div className="bg-gray-400 transition-all duration-300" style={{ width: width(blocked) }} />
      </div>

      {/* In-flight filenames — seeing several at once is the visible proof of
          parallelism. Shown only while the phase is still running. */}
      {!complete && inflight.length > 0 && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-gray-500">
          <svg className="h-3 w-3 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span className="font-mono truncate">
            {inflight.length} in flight: {inflight.join(', ')}
          </span>
        </div>
      )}

      {/* Partial-failure badge on completion. */}
      {complete && hasIssues && (
        <div className="mt-2 text-[11px] font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          ⚠️ {done} generated{failed ? `, ${failed} failed` : ''}{blocked ? `, ${blocked} blocked` : ''} — see the QA report at the review gate.
        </div>
      )}
    </div>
  )
}
