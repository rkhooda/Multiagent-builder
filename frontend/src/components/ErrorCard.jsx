import { useEffect, useState } from 'react'

const FRIENDLY_COPY = {
  rate_limit:
    'Every provider in the chain hit its free-tier rate limit. Free quotas usually reset within minutes (Groq/OpenRouter) or at midnight UTC (daily caps).',
  timeout: 'The model timed out twice, and the fallback did too. The provider may be overloaded right now.',
  auth: 'A provider rejected its API key. Check the keys in backend/.env — retrying will not help until they are fixed.',
  bad_output:
    'The model kept returning invalid output, even after a repair attempt. Retrying often works; free-tier models have off days.',
  agent_bug: 'An internal error in the agent code (not the LLM). The traceback is in the server logs — retrying will hit the same bug.',
}

const AGENT_LABELS = {
  research: 'Research', requirements: 'Requirements', architecture: 'Architecture',
  planning: 'Planning', frontend_code: 'Frontend Code', backend_code: 'Backend Code',
  database: 'Database', qa: 'QA', devops: 'DevOps',
}

/**
 * Red (error_paused) / amber (rate_limited) recovery card with
 * Retry / Skip / Cancel actions. Rate-limit cards show a live countdown to
 * the server's auto-retry with a "Retry Now" shortcut.
 */
export default function ErrorCard({ info, onRecover }) {
  // info: {agent, error_type, message, skippable, rate_limited, retry_in, cycle, max_cycles, receivedAt}
  const [showDetails, setShowDetails] = useState(false)
  const [submitting, setSubmitting] = useState(null)
  const [actionError, setActionError] = useState('')
  const [now, setNow] = useState(Date.now())

  const isRateLimit = info.rate_limited
  useEffect(() => {
    if (!isRateLimit) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [isRateLimit])

  const secondsLeft = isRateLimit
    ? Math.max(0, Math.round((info.receivedAt + (info.retry_in || 60) * 1000 - now) / 1000))
    : 0

  const agentLabel = AGENT_LABELS[info.agent] || info.agent || 'Pipeline'
  const friendly = FRIENDLY_COPY[info.error_type] || 'The pipeline hit an unexpected error and paused.'

  const act = async (action) => {
    setSubmitting(action)
    setActionError('')
    try {
      await onRecover(action)
    } catch (err) {
      setActionError(err.message || 'Recovery request failed')
      setSubmitting(null)
    }
  }

  const palette = isRateLimit
    ? { border: 'border-amber-300', bg: 'bg-amber-50', title: 'text-amber-800', badge: 'bg-amber-100 text-amber-800' }
    : { border: 'border-red-300', bg: 'bg-red-50', title: 'text-red-800', badge: 'bg-red-100 text-red-800' }

  return (
    <div className={`rounded-lg border ${palette.border} ${palette.bg} p-4 shadow-sm`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`text-lg ${isRateLimit ? '' : 'animate-none'}`}>{isRateLimit ? '⏳' : '🛑'}</span>
            <h3 className={`text-sm font-bold ${palette.title}`}>
              {isRateLimit ? `${agentLabel} agent is rate-limited` : `${agentLabel} agent failed`}
            </h3>
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${palette.badge}`}>
              {(info.error_type || 'error').replace('_', ' ')}
            </span>
          </div>
          <p className="mt-1.5 text-sm text-gray-700">{friendly}</p>
          {isRateLimit && (
            <p className="mt-1 text-xs font-medium text-amber-700">
              {secondsLeft > 0
                ? `Auto-retry in ${secondsLeft}s (cycle ${info.cycle || 1}/${info.max_cycles || 3})`
                : 'Auto-retrying now…'}
            </p>
          )}
        </div>
      </div>

      {info.message && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setShowDetails((v) => !v)}
            className="text-xs font-semibold text-gray-500 hover:text-gray-700"
          >
            {showDetails ? '▾ Hide details' : '▸ Details'}
          </button>
          {showDetails && (
            <pre className="mt-1 max-h-40 overflow-auto rounded border border-gray-200 bg-white p-2 font-mono text-[11px] leading-relaxed text-gray-600 whitespace-pre-wrap break-words">
              {info.message}
            </pre>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={submitting !== null}
          onClick={() => act('retry')}
          className="rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {submitting === 'retry' ? 'Retrying…' : isRateLimit ? 'Retry Now' : 'Retry This Agent'}
        </button>
        {info.skippable ? (
          <button
            type="button"
            disabled={submitting !== null}
            onClick={() => act('skip')}
            className="rounded border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {submitting === 'skip' ? 'Skipping…' : 'Skip This Agent'}
          </button>
        ) : (
          <span className="text-[11px] italic text-gray-500">
            Can't skip — downstream stages need {agentLabel.toLowerCase()}'s output.
          </span>
        )}
        <button
          type="button"
          disabled={submitting !== null}
          onClick={() => act('cancel')}
          className="ml-auto rounded border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50"
        >
          {submitting === 'cancel' ? 'Cancelling…' : 'Cancel Project'}
        </button>
      </div>
      {actionError && <p className="mt-2 text-xs font-medium text-red-600">{actionError}</p>}
    </div>
  )
}
