import { useEffect, useState } from 'react'
import { RESTART_STAGES, stageLabel } from '../lib/status'

const API = 'http://localhost:8000/api/projects'

function Modal({ title, children, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-lg rounded-lg bg-raised shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-line px-5 py-3">
          <h3 className="text-sm font-bold text-ink">{title}</h3>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  )
}

/**
 * Restart confirm. States exactly what is discarded and what is kept, and shows
 * the historical token cost of the agents that will re-run — restarting from
 * code generation re-runs every coder call, which on a free tier is the whole
 * budget for the day.
 */
export function RestartDialog({ projectId, projectName, onClose, onDone }) {
  const [stage, setStage] = useState('architecture')
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    fetch(`${API}/${projectId}/restart-preview?from_stage=${stage}`)
      .then(async (res) => {
        const body = await res.json()
        if (!res.ok) throw new Error(body?.detail || 'Could not load restart preview')
        if (!cancelled) setPreview(body)
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [projectId, stage])

  const submit = async () => {
    setSubmitting(true)
    setError('')
    try {
      const res = await fetch(`${API}/${projectId}/restart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_stage: stage }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body?.detail || `Restart failed (HTTP ${res.status})`)
      onDone?.(body)
      onClose()
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  const cost = preview?.cost_estimate

  return (
    <Modal title={`Restart "${projectName || projectId}"`} onClose={onClose}>
      <label className="block text-xs font-semibold uppercase tracking-wide text-ink-3">
        Re-run from
      </label>
      <select
        value={stage}
        onChange={(e) => setStage(e.target.value)}
        disabled={submitting}
        className="mt-1 w-full rounded border border-line-strong px-3 py-2 text-sm"
      >
        {RESTART_STAGES.map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>

      {loading ? (
        <p className="mt-4 text-sm text-ink-3">Checking what this would discard…</p>
      ) : preview ? (
        <div className="mt-4 space-y-3">
          <div className="rounded border border-err/35 bg-err/10 p-3">
            <p className="text-sm font-semibold text-err">This discards:</p>
            <p className="mt-1 text-sm text-err">
              {preview.discards.map(stageLabel).join(', ')}
              {preview.files_to_archive > 0
                ? ` — including ${preview.files_to_archive} generated file${preview.files_to_archive === 1 ? '' : 's'}`
                : ''}
            </p>
            {preview.files_to_archive > 0 && (
              <p className="mt-1 text-xs text-err">
                Discarded files are moved to <code>.archived/</code> inside the project, not deleted.
              </p>
            )}
          </div>

          <div className="rounded border border-ok/35 bg-ok/10 p-3">
            <p className="text-sm font-semibold text-ok">This keeps:</p>
            <p className="mt-1 text-sm text-ok">
              {preview.keeps.length ? preview.keeps.map(stageLabel).join(', ') : 'Nothing — this re-runs the whole pipeline.'}
            </p>
          </div>

          {cost && (
            <div className="rounded border border-warn/35 bg-warn/10 p-3">
              <p className="text-sm font-semibold text-warn">Estimated cost to re-run</p>
              <p className="mt-1 text-sm text-warn">
                ~{cost.estimated_tokens.toLocaleString()} tokens across {cost.estimated_calls} calls,
                based on what these agents already spent on this project.
              </p>
              {cost.agents_without_history.length > 0 && (
                <p className="mt-1 text-xs text-warn">
                  No history yet for {cost.agents_without_history.join(', ')} — the real cost will be higher.
                </p>
              )}
            </div>
          )}
        </div>
      ) : null}

      {error && <p className="mt-3 text-sm text-err">{error}</p>}

      <div className="mt-5 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          disabled={submitting}
          className="rounded border border-line-strong px-4 py-2 text-sm font-semibold text-ink hover:bg-overlay"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={submitting || loading || !preview}
          className="rounded bg-warn px-4 py-2 text-sm font-semibold text-ink hover:brightness-110 disabled:opacity-50"
        >
          {submitting ? 'Restarting…' : `Restart from ${stageLabel(stage)}`}
        </button>
      </div>
    </Modal>
  )
}

/**
 * Delete confirm. Deletion is a hard cascade across four stores, so the ZIP is
 * the user's only backup — the download is offered inline, above the type-to-
 * confirm, rather than mentioned after the fact.
 */
export function DeleteDialog({ projectId, projectName, status, onClose, onDone }) {
  const [typed, setTyped] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const isRunning = status === 'running'

  const submit = async () => {
    setSubmitting(true)
    setError('')
    try {
      const res = await fetch(`${API}/${projectId}`, { method: 'DELETE' })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(body?.detail || `Delete failed (HTTP ${res.status})`)
      onDone?.(body)
      onClose()
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  return (
    <Modal title={`Delete "${projectName || projectId}"`} onClose={onClose}>
      <div className="rounded border border-warn/35 bg-warn/10 p-3">
        <p className="text-sm font-semibold text-warn">Download the project ZIP first</p>
        <p className="mt-1 text-xs text-warn">
          This cannot be undone. Generated files, the run history and all metrics for this
          project are permanently removed.
        </p>
        <a
          href={`${API}/${projectId}/download`}
          className="mt-2 inline-block rounded border border-warn/45 bg-raised px-3 py-1.5 text-xs font-semibold text-warn hover:bg-warn/20"
        >
          ↓ Download ZIP
        </a>
      </div>

      {isRunning && (
        <p className="mt-3 rounded border border-run/35 bg-run/10 p-3 text-xs text-run">
          This project is still running. Deleting it will cancel the run first.
        </p>
      )}

      <label className="mt-4 block text-xs font-semibold text-ink">
        Type <span className="font-mono text-err">delete</span> to confirm
      </label>
      <input
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        disabled={submitting}
        autoFocus
        placeholder="delete"
        className="mt-1 w-full rounded border border-line-strong px-3 py-2 font-mono text-sm"
      />

      {error && <p className="mt-3 text-sm text-err">{error}</p>}

      <div className="mt-5 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          disabled={submitting}
          className="rounded border border-line-strong px-4 py-2 text-sm font-semibold text-ink hover:bg-overlay"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={submitting || typed.trim().toLowerCase() !== 'delete'}
          className="rounded bg-err px-4 py-2 text-sm font-semibold text-ink hover:brightness-110 disabled:opacity-40"
        >
          {submitting ? 'Deleting…' : 'Delete permanently'}
        </button>
      </div>
    </Modal>
  )
}
