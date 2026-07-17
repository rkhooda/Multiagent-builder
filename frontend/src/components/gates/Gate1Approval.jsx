import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import FeedbackInput from './FeedbackInput'
import RegeneratingOverlay from './RegeneratingOverlay'
import DiffView from './DiffView'
import RetryWarning, { RETRY_SOFT_CAP, retryCount, cappedButtonClass } from './RetryWarning'

function Panel({ title, children, className = '' }) {
  return (
    <div className={`flex flex-col rounded-lg border border-gray-200 bg-white ${className}`}>
      <div className="border-b border-gray-100 px-4 py-2">
        <h4 className="text-sm font-bold text-gray-800">{title}</h4>
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
    <dl className="mb-4 grid grid-cols-2 gap-x-4 gap-y-2 rounded border border-blue-100 bg-blue-50 p-3 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="col-span-2 sm:col-span-1">
          <dt className="font-semibold uppercase tracking-wide text-blue-700">{label}</dt>
          <dd className="text-gray-700">{value}</dd>
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
      await fetch(`http://localhost:8000/api/projects/${projectId}/state`, {
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
    <div className="space-y-4">
      <div className="flex items-center space-x-2">
        <span className="h-2.5 w-2.5 rounded-full bg-orange-500 animate-pulse" />
        <h3 className="text-base font-bold text-gray-900 uppercase tracking-wide">
          Gate 1 — Research &amp; Requirements Review
        </h3>
      </div>
      <p className="text-sm text-gray-600">
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
                  className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                >
                  {showResearchDiff ? 'Hide Changes' : 'View Changes'}
                </button>
              </div>
            )}
            {showResearchDiff && previousResearch ? (
              <DiffView oldText={previousResearch} newText={researchReport} />
            ) : (
              <div className="prose prose-sm max-w-none">
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
                  className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                >
                  {editing ? 'Cancel Edit' : '✏️ Edit'}
                </button>
                {previousRequirements && (
                  <button
                    type="button"
                    onClick={() => setShowDiff((v) => !v)}
                    disabled={Boolean(regenAction)}
                    className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
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
                  className="w-full rounded border border-gray-300 p-2 font-mono text-xs text-gray-900 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={saveEditing}
                    className="rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={discardEditing}
                    className="rounded border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                  >
                    Discard
                  </button>
                </div>
              </div>
            ) : (
              <div className="prose prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {requirementsDoc || 'Loading requirements...'}
                </ReactMarkdown>
              </div>
            )}
          </Panel>
        </div>
      </div>

      <div className="sticky bottom-0 space-y-3 border-t border-gray-100 bg-white pt-3">
        {confirmingCancel ? (
          <div className="rounded border border-red-200 bg-red-50 p-3 space-y-2">
            <p className="text-sm font-medium text-red-700">
              Cancel this project? This cannot be undone.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleCancelProject}
                disabled={isPending}
                className="rounded bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700"
              >
                {submitting === 'reject' ? 'Cancelling...' : 'Yes, cancel project'}
              </button>
              <button
                type="button"
                onClick={() => setConfirmingCancel(false)}
                disabled={isPending}
                className="rounded border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50"
              >
                Keep project
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={handleApprove}
              disabled={isPending || regenAction}
              className="flex-1 rounded bg-green-600 py-2 px-3 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-60"
            >
              {submitting === 'approve' ? 'Sending...' : '✅ Approve & Continue'}
            </button>
            <button
              type="button"
              onClick={() => requestRegenerate('edit')}
              disabled={isPending || regenAction}
              className={
                editCapped
                  ? cappedButtonClass
                  : 'flex-1 rounded border border-gray-300 bg-white py-2 px-3 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-60'
              }
            >
              {editCapped ? '⚠️ ' : '✏️ '}Request Changes
            </button>
            <button
              type="button"
              onClick={() => requestRegenerate('back')}
              disabled={isPending || regenAction}
              className={
                backCapped
                  ? cappedButtonClass
                  : 'flex-1 rounded border border-gray-300 bg-white py-2 px-3 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-60'
              }
            >
              {backCapped ? '⚠️ ' : '⬅️ '}Go Back to Research
            </button>
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={isPending || regenAction}
              className="rounded border border-red-200 bg-white py-2 px-3 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-60"
            >
              Cancel Project
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
