import { useEffect, useState } from 'react'

const API = 'http://localhost:8000/api/projects'

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
    <div className="rounded border border-gray-200 bg-gray-50 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`mt-0.5 text-sm font-bold ${accent || 'text-gray-800'}`}>
        {value}
        {sub && <span className="ml-1 text-[10px] font-normal text-gray-400">{sub}</span>}
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
      <div className="rounded-lg border border-gray-200 bg-white p-4 text-xs text-gray-500">
        Metrics unavailable ({error}). The run itself is unaffected.
      </div>
    )
  }
  if (!data) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-4 text-xs text-gray-400">
        Loading metrics…
      </div>
    )
  }

  // Projects generated before Day 23 have no rows — explain rather than show zeros.
  if (!data.has_metrics) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-gray-700">Run Metrics</h3>
        <p className="mt-1 text-xs text-gray-500">
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

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-gray-700">Run Metrics</h3>
        <span className="text-[10px] text-gray-400">
          {data.attempts} attempt{data.attempts === 1 ? '' : 's'}
          {data.failed_attempts > 0 && ` · ${data.failed_attempts} failed`}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Total Tokens" value={fmt(data.total_tokens)} accent="text-indigo-700" />
        <Stat label="Input Tokens" value={fmt(data.prompt_tokens)} sub="prompt" />
        <Stat label="Output Tokens" value={fmt(data.completion_tokens)} sub="completion" />
        <Stat label="LLM Wall-Clock" value={ms(data.total_latency_ms)} sub="summed" />
        <Stat label="Cost" value="$0.00" sub="free tiers" />
      </div>

      {missingUsage > 0 && (
        <p className="mt-2 text-[11px] text-amber-700">
          {missingUsage} attempt{missingUsage === 1 ? '' : 's'} returned no token usage from the
          provider and {missingUsage === 1 ? 'is' : 'are'} excluded from the averages below.
        </p>
      )}

      {!compact && rows.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-[10px] uppercase tracking-wide text-gray-500">
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
            <tbody className="text-gray-700">
              {rows.map((r) => (
                <tr key={r.agent} className="border-b border-gray-100 last:border-0">
                  <td className="py-1.5 pr-3 font-mono text-[11px]">{r.agent}</td>
                  <td className="py-1.5 pr-3">{r.calls}</td>
                  <td className={`py-1.5 pr-3 ${r.attempts > r.calls ? 'font-semibold text-amber-700' : ''}`}>
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
      )}
    </div>
  )
}
