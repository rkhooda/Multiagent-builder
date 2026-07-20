import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import MermaidDiagram from '../MermaidDiagram'
import FeedbackInput from './FeedbackInput'
import RegeneratingOverlay from './RegeneratingOverlay'
import DiffView from './DiffView'
import RetryWarning, { RETRY_SOFT_CAP, retryCount, cappedButtonClass } from './RetryWarning'

function splitArchitectureSections(doc) {
  const regex = /^##\s+(.+)$/gm
  const matches = [...doc.matchAll(regex)]
  if (matches.length === 0) return null

  const sections = {}
  matches.forEach((match, i) => {
    const heading = match[1].trim()
    const start = match.index + match[0].length
    const end = i + 1 < matches.length ? matches[i + 1].index : doc.length
    sections[heading] = doc.slice(start, end).trim()
  })
  return sections
}

function findSection(sections, keyword) {
  if (!sections) return null
  const key = Object.keys(sections).find((k) => k.toLowerCase().includes(keyword))
  return key ? sections[key] : null
}

function extractFencedBlock(text, lang) {
  const langPattern = lang ? lang : '[a-zA-Z]*'
  const regex = new RegExp('```' + langPattern + '\\s*([\\s\\S]*?)```', 'i')
  const match = text.match(regex)
  return match ? match[1].trim() : null
}

function extractMermaidBlocks(text) {
  const blocks = []
  const regex = /```mermaid\s*([\s\S]*?)```/gi
  let match
  while ((match = regex.exec(text)) !== null) {
    blocks.push(match[1].trim())
  }
  return blocks
}

function extractNonMermaidBlocks(text) {
  const blocks = []
  const regex = /```([a-zA-Z]*)\s*([\s\S]*?)```/g
  let match
  while ((match = regex.exec(text)) !== null) {
    if ((match[1] || '').toLowerCase() !== 'mermaid') {
      blocks.push(match[2].trim())
    }
  }
  return blocks
}

function MarkdownWithMermaid({ content }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ inline, className, children }) {
          const lang = /language-(\w+)/.exec(className || '')?.[1]
          if (!inline && lang === 'mermaid') {
            return <MermaidDiagram code={String(children)} />
          }
          return <code className={className}>{children}</code>
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

function Panel({ title, children }) {
  return (
    <div className="rounded-lg border border-line bg-raised">
      <div className="border-b border-line px-4 py-2">
        <h4 className="text-sm font-bold text-ink">{title}</h4>
      </div>
      <div className="max-h-[28rem] overflow-auto p-4">{children}</div>
    </div>
  )
}

// decision -> the stage its regeneration targets (drives retry-cap warnings)
const TARGET_STAGE = { edit: 'architecture', back: 'requirements' }
const TARGET_LABEL = { edit: 'Architecture', back: 'Requirements' }

export default function Gate2Approval({ projectId, projectState, status, onResume, onRefresh, lastAgentComplete }) {
  const [feedbackMode, setFeedbackMode] = useState(null) // 'edit' | 'back' | null
  const [warningFor, setWarningFor] = useState(null) // 'edit' | 'back' | null
  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [submitting, setSubmitting] = useState(null)
  const [regenAction, setRegenAction] = useState(null) // 'edit' | 'back' while re-running
  const [showDiff, setShowDiff] = useState(false)
  const [showRequirementsDiff, setShowRequirementsDiff] = useState(false)
  const hasLeftApprovalRef = useRef(false)

  const architectureDoc = projectState?.architecture_doc || ''
  const previousArchitecture = projectState?.previous_versions?.architecture_doc || ''
  const previousRequirements = projectState?.previous_versions?.requirements_doc || ''

  useEffect(() => {
    if (status !== 'awaiting_approval') {
      hasLeftApprovalRef.current = true
    } else if (hasLeftApprovalRef.current) {
      // Back at the gate after a feedback re-run — open the diff(s) so the
      // changes are immediately visible. A back-cycle changed requirements too.
      if (regenAction) setShowDiff(true)
      if (regenAction === 'back') setShowRequirementsDiff(true)
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

  // Soft-cap warning shown BEFORE opening the feedback input.
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

  const isPending = submitting !== null
  const regenLabel =
    regenAction === 'back'
      ? lastAgentComplete === 'requirements'
        ? 'Regenerating architecture from the new requirements…'
        : 'Rewriting requirements with your feedback…'
      : 'Regenerating architecture with your feedback…'
  const editCapped = retryCount(projectState, 'architecture') >= RETRY_SOFT_CAP
  const backCapped = retryCount(projectState, 'requirements') >= RETRY_SOFT_CAP

  const sections = splitArchitectureSections(architectureDoc)
  const folderSection = findSection(sections, 'folder')
  const apiSection = findSection(sections, 'api')
  const dbSection = findSection(sections, 'database') || findSection(sections, 'schema')
  const securitySection = findSection(sections, 'security')

  const parsedOk = Boolean(folderSection && apiSection && dbSection)

  const folderTree = parsedOk ? (extractFencedBlock(folderSection, '(?:text|bash)?') || folderSection) : null
  const dbDiagrams = parsedOk ? extractMermaidBlocks(dbSection) : []
  const sqlBlocks = parsedOk ? extractNonMermaidBlocks(dbSection) : []

  return (
    <div className="space-y-4">
      <div className="flex items-center space-x-2">
        <span className="h-2.5 w-2.5 rounded-full bg-accent animate-pulse" />
        <h3 className="text-base font-bold text-ink uppercase tracking-wide">
          Gate 2 — Architecture Review
        </h3>
      </div>
      <p className="text-sm text-ink-2">
        Review the system architecture below before the pipeline proceeds to planning.
      </p>

      <div className="relative">
        {regenAction && <RegeneratingOverlay label={regenLabel} />}

        {(previousArchitecture || previousRequirements) && (
          <div className="mb-3 flex gap-2">
            {previousArchitecture && (
              <button
                type="button"
                onClick={() => setShowDiff((v) => !v)}
                className="rounded border border-line-strong bg-raised px-2 py-1 text-xs font-semibold text-ink hover:bg-overlay"
              >
                {showDiff ? 'Hide Architecture Changes' : 'View Architecture Changes'}
              </button>
            )}
            {previousRequirements && (
              <button
                type="button"
                onClick={() => setShowRequirementsDiff((v) => !v)}
                className="rounded border border-line-strong bg-raised px-2 py-1 text-xs font-semibold text-ink hover:bg-overlay"
              >
                {showRequirementsDiff ? 'Hide Requirements Changes' : 'View Requirements Changes'}
              </button>
            )}
          </div>
        )}

        {showRequirementsDiff && previousRequirements && (
          <div className="mb-4">
            <Panel title="Requirements — changes from this cycle">
              <DiffView oldText={previousRequirements} newText={projectState?.requirements_doc || ''} />
            </Panel>
          </div>
        )}

        {showDiff && previousArchitecture ? (
          <DiffView oldText={previousArchitecture} newText={architectureDoc} />
        ) : !architectureDoc ? (
          <p className="text-sm text-ink-3">Loading architecture document...</p>
        ) : !parsedOk ? (
          <Panel title="Architecture Document">
            <div className="prose prose-sm max-w-none">
              <MarkdownWithMermaid content={architectureDoc} />
            </div>
          </Panel>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Panel title="Folder Structure">
              <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink">
                {folderTree}
              </pre>
            </Panel>

            <Panel title="API Endpoints">
              <div className="prose prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{apiSection}</ReactMarkdown>
              </div>
            </Panel>

            <Panel title="Database Schema">
              <div className="space-y-3">
                {dbDiagrams.length === 0 ? (
                  <p className="text-xs text-ink-3">No Mermaid ER diagram found.</p>
                ) : (
                  dbDiagrams.map((code, i) => (
                    <MermaidDiagram key={i} code={code} title={`Diagram ${i + 1}`} />
                  ))
                )}
                {sqlBlocks.length > 0 && (
                  <details className="rounded border border-line bg-overlay p-2">
                    <summary className="cursor-pointer text-xs font-semibold text-ink">
                      SQL CREATE TABLE statements
                    </summary>
                    {sqlBlocks.map((sql, i) => (
                      <pre key={i} className="mt-2 whitespace-pre-wrap break-words font-mono text-xs text-ink">
                        {sql}
                      </pre>
                    ))}
                  </details>
                )}
              </div>
            </Panel>

            <Panel title="Security Notes">
              <div className="prose prose-sm max-w-none">
                {securitySection ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{securitySection}</ReactMarkdown>
                ) : (
                  <p className="text-xs text-ink-3">No security section found.</p>
                )}
              </div>
            </Panel>
          </div>
        )}
      </div>

      <div className="sticky bottom-0 space-y-3 border-t border-line bg-raised pt-3">
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
                className="rounded bg-red-600 px-3 py-1.5 text-xs font-semibold text-ink hover:bg-red-700"
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
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={handleApprove}
              disabled={isPending || regenAction}
              className="flex-1 rounded bg-green-600 py-2 px-3 text-sm font-semibold text-ink hover:bg-green-700 disabled:opacity-60"
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
                  : 'flex-1 rounded border border-line-strong bg-raised py-2 px-3 text-sm font-semibold text-ink hover:bg-overlay disabled:opacity-60'
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
                  : 'flex-1 rounded border border-line-strong bg-raised py-2 px-3 text-sm font-semibold text-ink hover:bg-overlay disabled:opacity-60'
              }
            >
              {backCapped ? '⚠️ ' : '⬅️ '}Go Back to Requirements
            </button>
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={isPending || regenAction}
              className="rounded border border-err/35 bg-raised py-2 px-3 text-sm font-semibold text-err hover:bg-err/10 disabled:opacity-60"
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
            onDismiss={() => setWarningFor(null)}
          />
        )}

        {feedbackMode && !confirmingCancel && (
          <FeedbackInput
            label={
              feedbackMode === 'back'
                ? 'What should change in the requirements?'
                : 'What should change in the architecture?'
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
