import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import FeedbackInput from './FeedbackInput'
import RegeneratingOverlay from './RegeneratingOverlay'
import DiffView from './DiffView'
import RetryWarning, { RETRY_SOFT_CAP, retryCount, cappedButtonClass } from './RetryWarning'

function Panel({ title, children, className = '' }) {
  return (
    <div className={`flex flex-col rounded-lg border border-line bg-raised ${className}`}>
      <div className="border-b border-line px-4 py-2">
        <h4 className="text-sm font-bold text-ink">{title}</h4>
      </div>
      <div className="max-h-[36rem] overflow-auto p-4">{children}</div>
    </div>
  )
}

function TechStackCard({ techStackStr }) {
  let stack = null
  try {
    stack = techStackStr ? JSON.parse(techStackStr) : null
  } catch {
    stack = null
  }
  if (!stack) return null

  const rows = [
    ['Frontend', stack.frontend],
    ['Backend', stack.backend],
    ['Database', stack.database],
    ['Auth', stack.auth],
    ['Hosting', stack.hosting],
    ['Key Libraries', Array.isArray(stack.key_libraries) ? stack.key_libraries.join(', ') : stack.key_libraries],
  ].filter(([, v]) => v)

  return (
    <dl className="mb-4 grid grid-cols-2 gap-x-4 gap-y-2 rounded border border-run/25 bg-run/10 p-3 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="col-span-2 sm:col-span-1">
          <dt className="font-semibold uppercase tracking-wide text-run">{label}</dt>
          <dd className="text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

// decision -> the stage its regeneration targets (drives retry-cap warnings)
const TARGET_STAGE = { edit: 'requirements', back: 'research' }
const TARGET_LABEL = { edit: 'Requirements', back: 'Research' }

export default function Gate1Approval({ projectId, projectState, status, onResume, onRefresh, lastAgentComplete }) {
  const [feedbackMode, setFeedbackMode] = useState(null) // 'edit' | 'back' | null
  const [warningFor, setWarningFor] = useState(null) // 'edit' | 'back' | null
  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [submitting, setSubmitting] = useState(null)
  const [regenAction, setRegenAction] = useState(null) // 'edit' | 'back' while re-running
  const [editing, setEditing] = useState(false)
  const [draftText, setDraftText] = useState('')
  const [showDiff, setShowDiff] = useState(false)
  const [showResearchDiff, setShowResearchDiff] = useState(false)
  const hasLeftApprovalRef = useRef(false)

  const requirementsDoc = projectState?.requirements_doc || ''
  const researchReport = projectState?.research_report || ''
  const techStack = projectState?.tech_stack || ''
  const previousRequirements = projectState?.previous_versions?.requirements_doc || ''
  const previousResearch = projectState?.previous_versions?.research_report || ''

  useEffect(() => {
    if (status !== 'awaiting_approval') {
      hasLeftApprovalRef.current = true
    } else if (hasLeftApprovalRef.current) {
      // Back at the gate after a feedback re-run — open the diff(s) so the
      // changes are immediately visible. A back-cycle changed both documents.
      if (regenAction) setShowDiff(true)
      if (regenAction === 'back') setShowResearchDiff(true)
      setRegenAction(null)
      hasLeftApprovalRef.current = false
    }
  }, [status, regenAction])

  const handleApprove = async () => {
    setSubmitting('approve')
    try {
      await onResume('approve', '')
    } finally {
      setSubmitting(null)
    }
  }

  const handleSubmitFeedback = async (feedbackText) => {
    const action = feedbackMode
    setSubmitting(action)
    setRegenAction(action)
    try {
      await onResume(action, feedbackText)
      setFeedbackMode(null)
    } catch {
      setRegenAction(null)
    } finally {
      setSubmitting(null)
    }
  }

  const handleCancelProject = async () => {
    setSubmitting('reject')
    try {
      await onResume('reject', '')
    } finally {
      setSubmitting(null)
      setConfirmingCancel(false)
    }
  }

  // Show the soft-cap warning BEFORE opening the feedback input for a stage
  // that has already been regenerated RETRY_SOFT_CAP+ times.
  const requestRegenerate = (mode) => {
    setWarningFor(null)
    if (feedbackMode === mode) {
      setFeedbackMode(null)
      return
    }
    if (retryCount(projectState, TARGET_STAGE[mode]) >= RETRY_SOFT_CAP) {
      setFeedbackMode(null)
      setWarningFor(mode)
    } else {
      setFeedbackMode(mode)
    }
  }

  const startEditing = () => {
    setDraftText(requirementsDoc)
    setEditing(true)
  }

  const discardEditing = () => {
    setEditing(false)
    setDraftText('')
  }

  const saveEditing = async () => {
    try {
      await fetch(`/api/projects/${projectId}/state`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ field: 'requirements_doc', content: draftText }),
      })
      setEditing(false)
      if (onRefresh) await onRefresh()
    } catch (err) {
      console.error('Failed to save requirements edit:', err)
    }
  }

  const isPending = submitting !== null
  const regenLabel =
    regenAction === 'back'
      ? lastAgentComplete === 'research'
        ? 'Rewriting requirements from the new research…'
        : 'Re-researching with your feedback…'
      : 'Regenerating requirements with your feedback…'

  const editCapped = retryCount(projectState, 'requirements') >= RETRY_SOFT_CAP
  const backCapped = retryCount(projectState, 'research') >= RETRY_SOFT_CAP

  return (
    <div className="space-y-4 lg:grid lg:grid-cols-[minmax(0,1fr)_340px] lg:items-start lg:gap-5 lg:space-y-0">
      <div className="min-w-0 space-y-4">
      <div className="flex items-center space-x-2">
        <span className="h-2.5 w-2.5 rounded-full bg-accent animate-pulse" />
        <h3 className="text-base font-bold text-ink uppercase tracking-wide">
          Gate 1 — Research &amp; Requirements Review
        </h3>
      </div>
      <p className="text-sm text-ink-2">
        Review the research report and requirements document below before the pipeline proceeds to architecture.
      </p>

      <div className="relative">
        {regenAction && <RegeneratingOverlay label={regenLabel} />}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel title="Research Report">
            {previousResearch && (
              <div className="mb-3">
                <button
                  type="button"
                  onClick={() => setShowResearchDiff((v) => !v)}
                  className="rounded border border-line-strong bg-raised px-2 py-1 text-xs font-semibold text-ink hover:bg-overlay"
                >
                  {showResearchDiff ? 'Hide Changes' : 'View Changes'}
                </button>
              </div>
            )}
            {showResearchDiff && previousResearch ? (
              <DiffView oldText={previousResearch} newText={researchReport} />
            ) : (
              <div className="markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {researchReport || 'Loading research report...'}
                </ReactMarkdown>
              </div>
            )}
          </Panel>

          <Panel title="Requirements & Tech Stack">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => (editing ? discardEditing() : startEditing())}
                  disabled={Boolean(regenAction)}
                  className="rounded border border-line-strong bg-raised px-2 py-1 text-xs font-semibold text-ink hover:bg-overlay"
                >
                  {editing ? 'Cancel edit' : 'Edit'}
                </button>
                {previousRequirements && (
                  <button
                    type="button"
                    onClick={() => setShowDiff((v) => !v)}
                    disabled={Boolean(regenAction)}
                    className="rounded border border-line-strong bg-raised px-2 py-1 text-xs font-semibold text-ink hover:bg-overlay"
                  >
                    {showDiff ? 'Hide Changes' : 'View Changes'}
                  </button>
                )}
              </div>
            </div>

            {!editing && <TechStackCard techStackStr={techStack} />}

            {showDiff && previousRequirements ? (
              <DiffView oldText={previousRequirements} newText={requirementsDoc} />
            ) : editing ? (
              <div className="space-y-2">
                <textarea
                  value={draftText}
                  onChange={(e) => setDraftText(e.target.value)}
                  rows={18}
                  className="w-full rounded border border-line-strong p-2 font-mono text-xs text-ink focus:outline-none focus:ring-1 focus:ring-accent"
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={saveEditing}
                    className="rounded bg-overlay px-3 py-1.5 text-xs font-semibold text-ink hover:bg-line"
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={discardEditing}
                    className="rounded border border-line-strong bg-raised px-3 py-1.5 text-xs font-semibold text-ink hover:bg-overlay"
                  >
                    Discard
                  </button>
                </div>
              </div>
            ) : (
              <div className="markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {requirementsDoc || 'Loading requirements...'}
                </ReactMarkdown>
              </div>
            )}
          </Panel>
        </div>
      </div>

      </div>
      <div className="sticky bottom-0 z-10 space-y-3 border-t border-line bg-raised pt-3 lg:top-0 lg:bottom-auto lg:rounded-lg lg:border lg:p-4">
        {confirmingCancel ? (
          <div className="rounded border border-err/35 bg-err/10 p-3 space-y-2">
            <p className="text-sm font-medium text-err">
              Cancel this project? This cannot be undone.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleCancelProject}
                disabled={isPending}
                className="rounded bg-err px-3 py-1.5 text-xs font-semibold text-ink hover:brightness-110"
              >
                {submitting === 'reject' ? 'Cancelling...' : 'Yes, cancel project'}
              </button>
              <button
                type="button"
                onClick={() => setConfirmingCancel(false)}
                disabled={isPending}
                className="rounded border border-line-strong bg-raised px-3 py-1.5 text-xs font-semibold text-ink hover:bg-overlay"
              >
                Keep project
              </button>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
            <button
              type="button"
              onClick={handleApprove}
              disabled={isPending || regenAction}
              className="w-full rounded-md border border-accent bg-accent py-2 px-3 text-sm font-semibold text-accent-ink hover:brightness-110 disabled:opacity-50"
            >
              {submitting === 'approve' ? 'Approving…' : 'Approve & continue'}
            </button>
            <button
              type="button"
              onClick={() => requestRegenerate('edit')}
              disabled={isPending || regenAction}
              className={
                editCapped
                  ? cappedButtonClass
                  : 'flex-1 rounded border border-line-strong bg-raised py-2 px-3 text-sm font-semibold text-ink hover:bg-overlay disabled:opacity-60'
              }
            >
              Request changes
            </button>
            <button
              type="button"
              onClick={() => requestRegenerate('back')}
              disabled={isPending || regenAction}
              className={
                backCapped
                  ? cappedButtonClass
                  : 'flex-1 rounded border border-line-strong bg-raised py-2 px-3 text-sm font-semibold text-ink hover:bg-overlay disabled:opacity-60'
              }
            >
              Go back to Research
            </button>
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={isPending || regenAction}
              className="rounded border border-err/35 bg-raised py-2 px-3 text-sm font-semibold text-err hover:bg-err/10 disabled:opacity-60"
            >
              Cancel project
            </button>
          </div>
        )}

        {warningFor && !confirmingCancel && (
          <RetryWarning
            count={retryCount(projectState, TARGET_STAGE[warningFor])}
            stageLabel={TARGET_LABEL[warningFor]}
            onContinue={() => {
              setWarningFor(null)
              setFeedbackMode(warningFor)
            }}
            onEditDirectly={
              warningFor === 'edit'
                ? () => {
                    setWarningFor(null)
                    startEditing()
                  }
                : undefined
            }
            onDismiss={() => setWarningFor(null)}
          />
        )}

        {feedbackMode && !confirmingCancel && (
          <FeedbackInput
            label={
              feedbackMode === 'back'
                ? 'What should change in the research?'
                : 'What changes do you need?'
            }
            onSubmit={handleSubmitFeedback}
            onCancel={() => setFeedbackMode(null)}
            submitting={submitting === feedbackMode}
          />
        )}
      </div>
    </div>
  )
}
