import { useEffect, useState } from 'react'
import { DesktopOnly } from './ui'

const API = '/api/projects'

/**
 * Day 23 per-run observability panel.
 *
 * Everything runs on free tiers, so dollars are always $0.00 and are shown as a
 * footnote — TOKENS are the real budget line, and they are what determines
 * whether a run stays inside free-tier rate limits. The per-agent table is the
 * same data Day 26's optimisation work queries from metrics_store.
 *
 * Rows are per ATTEMPT, so a call that failed over from a timed-out primary
 * contributes its latency to the totals and its retry to the retries column,
 * which is why "attempts" can exceed the number of logical calls.
 */
function fmt(n) {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function ms(v) {
  if (v == null) return '—'
  return v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`
}

function Stat({ label, value, sub, accent }) {
  return (
    <div className="rounded border border-line bg-overlay px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-3">{label}</div>
      <div className={`mt-0.5 text-sm font-bold ${accent || 'text-ink'}`}>
        {value}
        {sub && <span className="ml-1 text-[10px] font-normal text-ink-3">{sub}</span>}
      </div>
    </div>
  )
}

export default function MetricsPanel({ projectId, compact = false }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!projectId) return
    let cancelled = false
    fetch(`${API}/${projectId}/metrics`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message))
    return () => { cancelled = true }
  }, [projectId])

  // A metrics outage must never look like a pipeline failure.
  if (error) {
    return (
      <div className="rounded-lg border border-line bg-raised p-4 text-xs text-ink-3">
        Metrics unavailable ({error}). The run itself is unaffected.
      </div>
    )
  }
  if (!data) {
    return (
      <div className="rounded-lg border border-line bg-raised p-4 text-xs text-ink-3">
        Loading metrics…
      </div>
    )
  }

  // Projects generated before Day 23 have no rows — explain rather than show zeros.
  if (!data.has_metrics) {
    return (
      <div className="rounded-lg border border-line bg-raised p-4">
        <h3 className="text-sm font-semibold text-ink">Run Metrics</h3>
        <p className="mt-1 text-xs text-ink-3">
          No metrics recorded for this project. Runs from before observability was
          added (Day 23) have no instrumentation data — re-run the pipeline to collect it.
        </p>
      </div>
    )
  }

  // by_agent counts SUCCESSFUL calls (averages are over those); latency_by_agent
  // counts every attempt. Keep them as distinct columns — merging them let the
  // attempt count overwrite the call count, so a row could read "5 calls"
  // beside averages computed from the single call that actually returned.
  const latency = Object.fromEntries((data.latency_by_agent || []).map((r) => [r.agent, r]))
  const rows = (data.by_agent || []).map((r) => ({
    ...r,
    attempts: latency[r.agent]?.calls,
    p50_ms: latency[r.agent]?.p50_ms,
    p95_ms: latency[r.agent]?.p95_ms,
  }))
  const missingUsage = rows.reduce((a, r) => a + (r.missing_usage || 0), 0)
  const cache = data.cache || {}
  // Cache hits are recorded as attempts with ~0 latency, so they would show up
  // as a phantom "cache" stage worth 0% of the time. Excluded from the breakdown.
  const timeRows = (data.latency_by_agent || []).filter((r) => r.total_ms > 0).slice(0, 8)
  const providers = (data.providers || []).filter((p) => p.tracked)
  // Local models are weaker than the cloud ones. A run that quietly fell back to
  // them produces worse output with no visible reason, so the share is stated
  // rather than left to be discovered — but only when it actually happened, so
  // an all-cloud run carries no banner at all.
  const local = data.local || {}
  const localPct = local.calls ? Math.round((local.local_calls / local.calls) * 100) : 0

  return (
    <div className="rounded-lg border border-line bg-raised p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
          Run Metrics
          {data.fast_mode && (
            <span
              className="rounded bg-warn/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warn"
              title="Generated in Fast Mode: lighter budgets, and defects were reported rather than repaired."
            >
              Fast Mode
            </span>
          )}
        </h3>
        <span className="text-[10px] text-ink-3">
          {data.attempts} attempt{data.attempts === 1 ? '' : 's'}
          {data.failed_attempts > 0 && ` · ${data.failed_attempts} failed`}
          {/* Distinct from failed: a tier passed over because its daily budget
              was gone, with no request sent. Every local call skips both cloud
              tiers, so lumping these in read as a broken run. */}
          {data.skipped_attempts > 0 && ` · ${data.skipped_attempts} skipped`}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Total Tokens" value={fmt(data.total_tokens)} accent="text-accent" />
        <Stat label="Input Tokens" value={fmt(data.prompt_tokens)} sub="prompt" />
        <Stat label="Output Tokens" value={fmt(data.completion_tokens)} sub="completion" />
        <Stat label="LLM Wall-Clock" value={ms(data.total_latency_ms)} sub="summed" />
        <Stat
          label="Cache Hits"
          value={cache.total ? `${Math.round((cache.hit_rate || 0) * 100)}%` : '—'}
          sub={cache.total ? `${cache.hits}/${cache.total}` : 'no data'}
          accent={cache.hits > 0 ? 'text-ok' : undefined}
        />
      </div>

      {/* Where the minutes went. Ordered by TOTAL time, not per-call latency:
          an agent called 43 times at 16s costs more than one called twice at
          84s, and only the total answers "what should I optimise next". */}
      {timeRows.length > 0 && (
        <div className="mt-4">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-3">
            Time Breakdown
          </div>
          <div className="mt-1.5 space-y-1">
            {timeRows.map((r) => (
              <div key={r.agent} className="flex items-center gap-2">
                <div className="w-28 flex-shrink-0 font-mono text-[11px] text-ink-2">
                  {r.agent}
                </div>
                <div className="h-3 flex-1 overflow-hidden rounded-sm bg-overlay">
                  <div
                    className="h-full rounded-sm bg-run"
                    style={{ width: `${Math.max(r.pct_of_total, 1)}%` }}
                  />
                </div>
                <div className="w-24 flex-shrink-0 text-right text-[11px] tabular-nums text-ink-2">
                  {ms(r.total_ms)}
                  <span className="ml-1 text-ink-3">{r.pct_of_total}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Free-tier daily allowances. Process-wide, not per project: quota is
          shared across every run, and exhausting it is what actually stops the
          pipeline (Day 26 hit Groq's 100k tokens/day ceiling). */}
      {providers.length > 0 && (
        <div className="mt-4">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-3">
            Provider Daily Budget
          </div>
          <div className="mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {providers.map((p) => {
              const pct = Math.min(p.pct_used ?? 0, 100)
              const danger = (p.pct_used ?? 0) >= 90
              return (
                <div key={p.provider} className="flex items-center gap-2">
                  <div className="w-24 flex-shrink-0 font-mono text-[11px] text-ink-2">
                    {p.provider}
                  </div>
                  <div className="h-3 flex-1 overflow-hidden rounded-sm bg-overlay">
                    <div
                      className={`h-full rounded-sm ${danger ? 'bg-err' : 'bg-ok'}`}
                      style={{ width: `${Math.max(pct, 1)}%` }}
                    />
                  </div>
                  <div
                    className={`w-28 flex-shrink-0 text-right text-[11px] tabular-nums ${
                      danger ? 'font-semibold text-err' : 'text-ink-2'
                    }`}
                  >
                    {fmt(p.remaining)} left
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {local.local_calls > 0 && (
        <p className="mt-3 rounded border-l-4 border-warn bg-warn/10 px-3 py-2 text-[11px] text-warn">
          <span className="font-semibold">
            {local.local_calls} of {local.calls} call{local.calls === 1 ? '' : 's'} ran on local
            models
          </span>{' '}
          ({localPct}%{local.models?.length ? ` · ${local.models.join(', ')}` : ''}). Local models
          are smaller than the cloud ones, so output quality may be reduced — this run cost $0 and
          consumed no quota. Re-run with <code className="font-mono">LLM_MODE=cloud-only</code> for
          the best results.
        </p>
      )}

      {(data.truncations || []).length > 0 && (
        <p className="mt-3 rounded border-l-4 border-warn bg-warn/10 px-3 py-2 text-[11px] text-warn">
          <span className="font-semibold">
            {data.truncations.length} output{data.truncations.length === 1 ? '' : 's'} hit the
            token ceiling
          </span>{' '}
          and {data.truncations.length === 1 ? 'was' : 'were'} cut off mid-content
          ({[...new Set(data.truncations.map((t) => t.agent))].join(', ')}). Raise that
          agent&rsquo;s max_tokens — the file or document is incomplete.
        </p>
      )}

      {missingUsage > 0 && (
        <p className="mt-2 text-[11px] text-warn">
          {missingUsage} attempt{missingUsage === 1 ? '' : 's'} returned no token usage from the
          provider and {missingUsage === 1 ? 'is' : 'are'} excluded from the averages below.
        </p>
      )}

      {!compact && rows.length > 0 && (
        <DesktopOnly label="The per-agent breakdown">
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead>
              <tr className="border-b border-line text-[10px] uppercase tracking-wide text-ink-3">
                <th className="py-1.5 pr-3 font-semibold">Agent</th>
                <th className="py-1.5 pr-3 font-semibold" title="Successful calls — averages are over these">
                  Calls
                </th>
                <th className="py-1.5 pr-3 font-semibold" title="All attempts, including retries and fallovers">
                  Att.
                </th>
                <th className="py-1.5 pr-3 font-semibold">Avg In</th>
                <th className="py-1.5 pr-3 font-semibold">Avg Out</th>
                <th className="py-1.5 pr-3 font-semibold">p50</th>
                <th className="py-1.5 pr-3 font-semibold">p95</th>
                <th className="py-1.5 font-semibold">Total Tokens</th>
              </tr>
            </thead>
            <tbody className="text-ink">
              {rows.map((r) => (
                <tr key={r.agent} className="border-b border-line last:border-0">
                  <td className="py-1.5 pr-3 font-mono text-[11px]">
                    {r.agent}
                    {/* Per-agent, because "the run used local models" does not
                        tell you WHICH artifact to distrust. Architecture having
                        run locally is the fact worth surfacing. */}
                    {local.agents?.[r.agent] > 0 && (
                      <span
                        className="ml-1.5 rounded bg-warn/10 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-warn"
                        title={`${local.agents[r.agent]} of this agent's calls ran on a local model`}
                      >
                        local
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3">{r.calls}</td>
                  <td className={`py-1.5 pr-3 ${r.attempts > r.calls ? 'font-semibold text-warn' : ''}`}>
                    {r.attempts ?? '—'}
                  </td>
                  <td className="py-1.5 pr-3">{fmt(r.avg_prompt_tokens)}</td>
                  <td className="py-1.5 pr-3">{fmt(r.avg_completion_tokens)}</td>
                  <td className="py-1.5 pr-3">{ms(r.p50_ms)}</td>
                  <td className="py-1.5 pr-3">{ms(r.p95_ms)}</td>
                  <td className="py-1.5">
                    {fmt((r.total_prompt_tokens || 0) + (r.total_completion_tokens || 0))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </DesktopOnly>
      )}
    </div>
  )
}
