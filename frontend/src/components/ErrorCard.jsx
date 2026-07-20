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
    ? { border: 'border-warn/45', bg: 'bg-warn/10', title: 'text-warn', badge: 'bg-warn/10 text-warn' }
    : { border: 'border-err/45', bg: 'bg-err/10', title: 'text-err', badge: 'bg-err/10 text-err' }

  return (
    <div className={`rounded-lg border ${palette.border} ${palette.bg} p-4 `}>
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
          <p className="mt-1.5 text-sm text-ink">{friendly}</p>
          {isRateLimit && (
            <p className="mt-1 text-xs font-medium text-warn">
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
            className="text-xs font-semibold text-ink-3 hover:text-ink"
          >
            {showDetails ? '▾ Hide details' : '▸ Details'}
          </button>
          {showDetails && (
            <pre className="mt-1 max-h-40 overflow-auto rounded border border-line bg-raised p-2 font-mono text-[11px] leading-relaxed text-ink-2 whitespace-pre-wrap break-words">
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
          className="rounded bg-overlay px-3 py-1.5 text-xs font-semibold text-ink hover:bg-line disabled:opacity-50"
        >
          {submitting === 'retry' ? 'Retrying…' : isRateLimit ? 'Retry Now' : 'Retry This Agent'}
        </button>
        {info.skippable ? (
          <button
            type="button"
            disabled={submitting !== null}
            onClick={() => act('skip')}
            className="rounded border border-line-strong bg-raised px-3 py-1.5 text-xs font-semibold text-ink hover:bg-overlay disabled:opacity-50"
          >
            {submitting === 'skip' ? 'Skipping…' : 'Skip This Agent'}
          </button>
        ) : (
          <span className="text-[11px] italic text-ink-3">
            Can't skip — downstream stages need {agentLabel.toLowerCase()}'s output.
          </span>
        )}
        <button
          type="button"
          disabled={submitting !== null}
          onClick={() => act('cancel')}
          className="ml-auto rounded border border-err/35 bg-raised px-3 py-1.5 text-xs font-semibold text-err hover:bg-err/10 disabled:opacity-50"
        >
          {submitting === 'cancel' ? 'Cancelling…' : 'Cancel Project'}
        </button>
      </div>
      {actionError && <p className="mt-2 text-xs font-medium text-err">{actionError}</p>}
    </div>
  )
}
