import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import FeedbackInput from './FeedbackInput'
import RegeneratingOverlay from './RegeneratingOverlay'
import DiffView from './DiffView'

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

export default function Gate1Approval({ projectId, projectState, status, onResume, onRefresh }) {
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [submitting, setSubmitting] = useState(null)
  const [regenerating, setRegenerating] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draftText, setDraftText] = useState('')
  const [showDiff, setShowDiff] = useState(false)
  const hasLeftApprovalRef = useRef(false)

  const requirementsDoc = projectState?.requirements_doc || ''
  const researchReport = projectState?.research_report || ''
  const techStack = projectState?.tech_stack || ''
  const previousRequirements = projectState?.previous_versions?.requirements_doc || ''

  useEffect(() => {
    if (status !== 'awaiting_approval') {
      hasLeftApprovalRef.current = true
    } else if (hasLeftApprovalRef.current) {
      // Back at the gate after a feedback re-run — open the diff so the
      // changes are immediately visible.
      if (regenerating) setShowDiff(true)
      setRegenerating(false)
      hasLeftApprovalRef.current = false
    }
  }, [status, regenerating])

  const handleApprove = async () => {
    setSubmitting('approve')
    try {
      await onResume('approve', '')
    } finally {
      setSubmitting(null)
    }
  }

  const handleSubmitFeedback = async (feedbackText) => {
    setSubmitting('edit')
    setRegenerating(true)
    try {
      await onResume('edit', feedbackText)
      setFeedbackOpen(false)
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

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Research Report">
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {researchReport || 'Loading research report...'}
            </ReactMarkdown>
          </div>
        </Panel>

        <Panel title="Requirements & Tech Stack" className="relative">
          {regenerating && <RegeneratingOverlay />}

          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => (editing ? discardEditing() : startEditing())}
                disabled={regenerating}
                className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
              >
                {editing ? 'Cancel Edit' : '✏️ Edit'}
              </button>
              {previousRequirements && (
                <button
                  type="button"
                  onClick={() => setShowDiff((v) => !v)}
                  disabled={regenerating}
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
          <div className="flex gap-3">
            <button
              type="button"
              onClick={handleApprove}
              disabled={isPending || regenerating}
              className="flex-1 rounded bg-green-600 py-2 px-3 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-60"
            >
              {submitting === 'approve' ? 'Sending...' : '✅ Approve & Continue'}
            </button>
            <button
              type="button"
              onClick={() => setFeedbackOpen((v) => !v)}
              disabled={isPending || regenerating}
              className="flex-1 rounded border border-gray-300 bg-white py-2 px-3 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-60"
            >
              ✏️ Request Changes
            </button>
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={isPending || regenerating}
              className="rounded border border-red-200 bg-white py-2 px-3 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-60"
            >
              Cancel Project
            </button>
          </div>
        )}

        {feedbackOpen && !confirmingCancel && (
          <FeedbackInput
            onSubmit={handleSubmitFeedback}
            onCancel={() => setFeedbackOpen(false)}
            submitting={submitting === 'edit'}
          />
        )}
      </div>
    </div>
  )
}
