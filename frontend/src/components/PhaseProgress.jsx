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

// Improvement 02: incremental QA runs DURING generation. Streaming
// qa_batch_complete events carry monotonic counters, so the latest one is the
// whole truth — idempotent under reconnect/replay, same principle as the file
// count snapshots above. Returns null when nothing streamed (batch mode).
export function deriveQaStream(events) {
  let latest = null
  for (const e of events) {
    if (e.type === 'qa_batch_complete' && e.streaming) latest = e
  }
  if (!latest) return null
  return {
    reviewed: latest.files_reviewed ?? 0,
    enqueued: latest.files_enqueued ?? 0,
    issues: latest.issues_found_so_far ?? 0,
  }
}

// Compact companion line under the generation bars — the overlap made visible.
export function QaStreamIndicator({ qa, generating }) {
  if (!qa) return null
  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-line bg-raised px-4 py-2 text-[11px] text-ink-3">
      {generating && (
        <svg className="h-3 w-3 animate-spin text-run" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      <span className="font-mono">
        QA: reviewing ({qa.reviewed} of {qa.enqueued} files
        {qa.issues > 0 ? `, ${qa.issues} issue${qa.issues === 1 ? '' : 's'} so far` : ''})
      </span>
    </div>
  )
}

const LABELS = { frontend: 'frontend', backend: 'backend', code: 'code' }

export default function PhaseProgress({ phase }) {
  const { phase: name, done, failed, blocked, total, inflight, complete, hasIssues } = phase
  if (!total) return null
  const width = (n) => `${(n / total) * 100}%`
  const label = LABELS[name] || name

  return (
    <div className="rounded-lg border border-line bg-raised px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-ink">
          {complete ? 'Generated' : 'Generating'} {label}: {done} of {total} files
        </span>
        <div className="flex items-center gap-2 text-[11px] font-mono">
          {failed > 0 && (
            <span className="rounded border border-err/40 bg-err/10 px-1.5 py-0.5 text-err">
              {failed} failed
            </span>
          )}
          {blocked > 0 && (
            <span className="rounded border border-line-strong bg-overlay px-1.5 py-0.5 text-ink-3">
              {blocked} blocked
            </span>
          )}
        </div>
      </div>

      {/* Segmented bar: green done, red failed, grey blocked, over a light track. */}
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-overlay">
        <div className="bg-ok transition-[width] duration-500 ease-out" style={{ width: width(done) }} />
        <div className="bg-err transition-[width] duration-500 ease-out" style={{ width: width(failed) }} />
        <div className="bg-idle transition-[width] duration-500 ease-out" style={{ width: width(blocked) }} />
      </div>

      {/* In-flight filenames — seeing several at once is the visible proof of
          parallelism. Shown only while the phase is still running. */}
      {!complete && inflight.length > 0 && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-3">
          <svg className="h-3 w-3 animate-spin text-run" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span className="truncate font-mono">
            {inflight.length} in flight: {inflight.join(', ')}
          </span>
        </div>
      )}

      {/* Partial-failure badge on completion. */}
      {complete && hasIssues && (
        <div className="mt-2 rounded border border-warn/35 bg-warn/10 px-2 py-1 text-[11px] text-warn">
          ⚠️ {done} generated{failed ? `, ${failed} failed` : ''}{blocked ? `, ${blocked} blocked` : ''} — see the QA report at the review gate.
        </div>
      )}
    </div>
  )
}
