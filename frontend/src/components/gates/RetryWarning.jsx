export const RETRY_SOFT_CAP = 3

export function retryCount(projectState, stage) {
  return projectState?.retry_counts?.[stage] || 0
}

// Amber button styling applied to Request Changes / Go Back buttons once a
// stage has hit the soft cap — the user sees the warning state BEFORE clicking.
export const cappedButtonClass =
  'flex-1 rounded border border-warn bg-warn/10 py-2 px-3 text-sm font-semibold text-warn hover:bg-amber-100 disabled:opacity-60'

/**
 * Inline notice shown when the user clicks a regenerate action for a stage
 * already regenerated RETRY_SOFT_CAP+ times. Never blocks — Continue Anyway
 * always proceeds; Edit Directly jumps to inline editing where available.
 */
export default function RetryWarning({ count, stageLabel, onContinue, onEditDirectly, onDismiss }) {
  return (
    <div className="space-y-2 rounded border border-warn/45 bg-warn/10 p-3">
      <p className="text-sm text-warn">
        ⚠️ {stageLabel} has been regenerated {count} times. Consider editing the output directly
        instead — repeated regeneration rarely converges.
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onContinue}
          className="rounded bg-amber-600 px-3 py-1.5 text-xs font-semibold text-ink hover:bg-amber-700"
        >
          Continue Anyway
        </button>
        {onEditDirectly && (
          <button
            type="button"
            onClick={onEditDirectly}
            className="rounded border border-warn bg-raised px-3 py-1.5 text-xs font-semibold text-warn hover:bg-amber-100"
          >
            Edit Directly Instead
          </button>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="rounded border border-line-strong bg-raised px-3 py-1.5 text-xs font-semibold text-ink-2 hover:bg-overlay"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
