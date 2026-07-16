import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import MermaidDiagram from '../MermaidDiagram'
import FeedbackInput from './FeedbackInput'
import RegeneratingOverlay from './RegeneratingOverlay'
import DiffView from './DiffView'

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
    <div className="rounded-lg border border-gray-200 bg-white">
      <div className="border-b border-gray-100 px-4 py-2">
        <h4 className="text-sm font-bold text-gray-800">{title}</h4>
      </div>
      <div className="max-h-[28rem] overflow-auto p-4">{children}</div>
    </div>
  )
}

export default function Gate2Approval({ projectId, projectState, status, onResume, onRefresh }) {
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [submitting, setSubmitting] = useState(null)
  const [regenerating, setRegenerating] = useState(false)
  const [showDiff, setShowDiff] = useState(false)
  const hasLeftApprovalRef = useRef(false)

  const architectureDoc = projectState?.architecture_doc || ''
  const previousArchitecture = projectState?.previous_versions?.architecture_doc || ''

  useEffect(() => {
    if (status !== 'awaiting_approval') {
      hasLeftApprovalRef.current = true
    } else if (hasLeftApprovalRef.current) {
      setRegenerating(false)
      hasLeftApprovalRef.current = false
    }
  }, [status])

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

  const isPending = submitting !== null

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
        <span className="h-2.5 w-2.5 rounded-full bg-orange-500 animate-pulse" />
        <h3 className="text-base font-bold text-gray-900 uppercase tracking-wide">
          Gate 2 — Architecture Review
        </h3>
      </div>
      <p className="text-sm text-gray-600">
        Review the system architecture below before the pipeline proceeds to planning.
      </p>

      <div className="relative">
        {regenerating && <RegeneratingOverlay label="Regenerating architecture with your feedback…" />}

        {previousArchitecture && (
          <div className="mb-3">
            <button
              type="button"
              onClick={() => setShowDiff((v) => !v)}
              className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
            >
              {showDiff ? 'Hide Changes' : 'View Changes'}
            </button>
          </div>
        )}

        {showDiff && previousArchitecture ? (
          <DiffView oldText={previousArchitecture} newText={architectureDoc} />
        ) : !architectureDoc ? (
          <p className="text-sm text-gray-500">Loading architecture document...</p>
        ) : !parsedOk ? (
          <Panel title="Architecture Document">
            <div className="prose prose-sm max-w-none">
              <MarkdownWithMermaid content={architectureDoc} />
            </div>
          </Panel>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Panel title="Folder Structure">
              <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-gray-700">
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
                  <p className="text-xs text-gray-500">No Mermaid ER diagram found.</p>
                ) : (
                  dbDiagrams.map((code, i) => (
                    <MermaidDiagram key={i} code={code} title={`Diagram ${i + 1}`} />
                  ))
                )}
                {sqlBlocks.length > 0 && (
                  <details className="rounded border border-gray-200 bg-gray-50 p-2">
                    <summary className="cursor-pointer text-xs font-semibold text-gray-700">
                      SQL CREATE TABLE statements
                    </summary>
                    {sqlBlocks.map((sql, i) => (
                      <pre key={i} className="mt-2 whitespace-pre-wrap break-words font-mono text-xs text-gray-700">
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
                  <p className="text-xs text-gray-500">No security section found.</p>
                )}
              </div>
            </Panel>
          </div>
        )}
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
