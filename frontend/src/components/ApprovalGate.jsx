import React, { useEffect, useState } from 'react'
import MermaidDiagram from './MermaidDiagram'

function ScrollableOutput({ children, className = '' }) {
  return (
    <div className={`rounded border border-gray-200 bg-gray-50 ${className}`}>
      <div className="max-h-[32rem] overflow-auto p-4 text-sm text-gray-700 whitespace-pre-wrap">
        {children}
      </div>
    </div>
  )
}

export default function ApprovalGate({ status, gateEvent, currentStage, eventsCount, onResume, projectId, initialProjectState }) {
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [feedbackText, setFeedbackText] = useState('')
  const [submitting, setSubmitting] = useState(null) // 'approve' | 'edit' | null
  const [projectState, setProjectState] = useState(null)
  const [activeTab, setActiveTab] = useState('requirements')
  const [selectedFile, setSelectedFile] = useState('')

  const gateName = projectState?.next_gate || gateEvent?.gate || ''

  useEffect(() => {
    if (initialProjectState) {
      setProjectState(initialProjectState)
    }
  }, [initialProjectState])

  useEffect(() => {
    if (projectId) {
      fetch(`http://localhost:8000/api/projects/${projectId}`)
        .then((r) => r.json())
        .then((state) => {
          setProjectState(state)
        })
        .catch((err) => console.error('Failed to load project state:', err))
    }
  }, [gateEvent, projectId, status, currentStage])

  useEffect(() => {
    if (gateName === 'human_gate_1') {
      setActiveTab('requirements')
    } else if (gateName === 'human_gate_2') {
      setActiveTab('architecture')
    }
  }, [gateName])

  const architectureDoc = projectState?.architecture_doc || ''
  const generatedFiles = projectState?.generated_files || {}
  const generatedFileEntries = Object.entries(generatedFiles)
  const plannedFileEntries = (projectState?.file_list || []).map((file) => [file, ''])
  const fileEntries = generatedFileEntries.length > 0 ? generatedFileEntries : plannedFileEntries
  const activeFile = selectedFile && fileEntries.some(([file]) => file === selectedFile)
    ? selectedFile
    : fileEntries[0]?.[0] || ''
  const activeFileContent = fileEntries.find(([file]) => file === activeFile)?.[1] || ''
  const hasGeneratedFiles = generatedFileEntries.length > 0
  const mermaidBlocks = []
  const mermaidRegex = /```mermaid\s*([\s\S]*?)```/gi
  let mermaidMatch
  while ((mermaidMatch = mermaidRegex.exec(architectureDoc)) !== null) {
    mermaidBlocks.push(mermaidMatch[1].trim())
  }

  const apiEndpointCount = (() => {
    const apiSectionMatch = architectureDoc.match(/##\s*API Endpoints([\s\S]*?)(?:\n##\s|$)/i)
    if (!apiSectionMatch) return 0
    const rows = apiSectionMatch[1]
      .split('\n')
      .filter((line) => /^\|/.test(line.trim()))
      .filter((line) => !line.includes(':---'))
    return rows.length > 0 ? Math.max(rows.length - 1, 0) : 0
  })()

  useEffect(() => {
    if (!activeFile && fileEntries.length > 0) {
      setSelectedFile(fileEntries[0][0])
    }
  }, [activeFile, fileEntries])

  const renderFileBrowser = () => {
    if (fileEntries.length === 0) return null

    return (
      <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h5 className="text-sm font-bold text-gray-800">
              {hasGeneratedFiles ? 'Generated Files' : 'Files to Generate'}
            </h5>
            <p className="text-xs text-gray-500">
              {hasGeneratedFiles
                ? 'Click a file to preview the generated code.'
                : 'Architecture has identified these files; code contents will appear after coder agents generate them.'}
            </p>
          </div>
          <span className="rounded bg-white px-2 py-1 font-mono text-xs font-semibold text-gray-600">
            {fileEntries.length}
          </span>
        </div>

        <div className="grid gap-3 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="max-h-72 overflow-y-auto rounded border border-gray-200 bg-white p-2">
            {fileEntries.map(([file]) => {
              const isActive = file === activeFile
              const isBackend = file.includes('backend') || file.endsWith('.py')
              const isFrontend = file.includes('frontend') || file.includes('src/') || file.endsWith('.jsx') || file.endsWith('.tsx')
              const accent = isBackend ? 'text-blue-700' : isFrontend ? 'text-green-700' : 'text-orange-700'

              return (
                <button
                  key={file}
                  type="button"
                  onClick={() => setSelectedFile(file)}
                  className={`mb-1 block w-full rounded px-2 py-1.5 text-left font-mono text-[11px] leading-snug transition-colors ${
                    isActive
                      ? 'bg-gray-900 text-white'
                      : `bg-white hover:bg-gray-100 ${accent}`
                  }`}
                  title={file}
                >
                  {file}
                </button>
              )
            })}
          </div>

          <div className="max-h-72 overflow-auto rounded border border-gray-200 bg-white p-3">
            <div className="mb-2 break-all font-mono text-xs font-semibold text-gray-700">
              {activeFile || 'No file selected'}
            </div>
            {hasGeneratedFiles ? (
              <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-gray-700">
                {activeFileContent || '// This file is present but has no generated content yet.'}
              </pre>
            ) : (
              <div className="rounded border border-dashed border-gray-300 bg-gray-50 p-3 text-xs leading-relaxed text-gray-500">
                This file is planned by the architecture agent. Once coder agents run, the generated code for this path will show here.
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }
  
  // Derive gate title and description
  const getGateInfo = (name) => {
    switch (name) {
      case 'human_gate_1':
        return {
          title: 'Gate 1 — Research & Requirements Review',
          desc: 'The agents have completed the research phase and gathered initial requirements. Please review the findings to proceed.'
        }
      case 'human_gate_2':
        return {
          title: 'Gate 2 — Architecture Review',
          desc: 'The tech stack and database schema have been determined. Review the system architecture blueprint.'
        }
      case 'human_gate_3':
        return {
          title: 'Gate 3 — Design & Plan Review',
          desc: 'The step-by-step implementation plan is ready. Review the exact file-by-file build sequence before code generation begins.'
        }
      case 'human_gate_4':
        return {
          title: 'Gate 4 — Final Deployment Review',
          desc: 'The code files have been written, database integrated, QA tests run, and DevOps configured. Perform the final check.'
        }
      default:
        // Try parsing number
        const num = name.replace(/[^0-9]/g, '')
        return {
          title: `Gate ${num || 'Review'} — Custom Stage Review`,
          desc: `Please review the artifacts generated for ${name} before proceeding.`
        }
    }
  }

  const handleApprove = async () => {
    setSubmitting('approve')
    try {
      await onResume('approve', '')
      setFeedbackOpen(false)
      setFeedbackText('')
    } catch (err) {
      console.error(err)
    } finally {
      setSubmitting(null)
    }
  }

  const handleSubmitChanges = async () => {
    if (!feedbackText.trim()) return
    setSubmitting('edit')
    try {
      await onResume('edit', feedbackText)
      setFeedbackOpen(false)
      setFeedbackText('')
    } catch (err) {
      console.error(err)
    } finally {
      setSubmitting(null)
    }
  }

  // Render based on state.
  // `interrupted` must be handled before the fallthrough: the default branch
  // claims "Agents working…", which would sit directly under the banner saying
  // the run stopped — the exact dishonest spinner this state exists to prevent.
  if (status === 'interrupted') {
    return (
      <div className="space-y-2 rounded-lg border border-purple-200 bg-white p-6 text-center">
        <div className="text-3xl">⏸</div>
        <h3 className="text-sm font-bold text-gray-900">Run interrupted</h3>
        <p className="text-xs text-gray-600">
          No agents are running. Use Resume above to continue from the last checkpoint.
        </p>
      </div>
    )
  }

  if (status === 'cancelled') {
    return (
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6 text-center space-y-3">
        <div className="text-4xl">🚫</div>
        <h3 className="text-xl font-bold text-gray-900">Project Cancelled</h3>
        <p className="text-sm text-gray-600">
          This project was cancelled at a human approval gate and will not continue.
        </p>
      </div>
    )
  }

  if (status === 'done' || status === 'completed') {
    return (
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6 text-center space-y-4">
        <div className="text-4xl">🎉</div>
        <h3 className="text-xl font-bold text-gray-900">Project Complete!</h3>
        <p className="text-sm text-gray-600">
          All agents have finished executing the pipeline. Your project is ready.
        </p>
        <div className="pt-2">
          <a
            href={`http://localhost:8000/api/projects/${projectId}/download`}
            className="block w-full rounded border border-blue-200 bg-blue-600 py-2.5 px-4 text-center text-sm font-semibold text-white hover:bg-blue-700"
          >
            ⬇ Download Project ZIP
          </a>
        </div>
      </div>
    )
  }

  if (status === 'awaiting_approval' && gateName) {
    const { title, desc } = getGateInfo(gateName)
    const isPending = submitting !== null

    return (
      <div className="bg-white rounded-lg border border-orange-200 shadow-sm p-6 space-y-4">
        <div className="flex items-center space-x-2">
          <span className="h-2.5 w-2.5 rounded-full bg-orange-500 animate-pulse" />
          <h3 className="text-base font-bold text-gray-900 uppercase tracking-wide">
            Human Gate Active
          </h3>
        </div>

        <h4 className="text-lg font-bold text-gray-800">{title}</h4>
        <p className="text-sm text-gray-600 leading-relaxed">{desc}</p>

        {gateName === 'human_gate_1' && (
          <div className="mt-4">
            <div className="flex gap-2 mb-3">
              <button
                onClick={() => setActiveTab('requirements')}
                className={`px-3 py-1 text-sm rounded font-medium ${
                  activeTab === 'requirements'
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                Requirements Doc
              </button>
              <button
                onClick={() => setActiveTab('research')}
                className={`px-3 py-1 text-sm rounded font-medium ${
                  activeTab === 'research'
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                Research Report
              </button>
              <button
                onClick={() => setActiveTab('techstack')}
                className={`px-3 py-1 text-sm rounded font-medium ${
                  activeTab === 'techstack'
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                Tech Stack
              </button>
            </div>

            <ScrollableOutput>
              {activeTab === 'requirements' && (projectState?.requirements_doc || 'Loading requirements...')}
              {activeTab === 'research' && (projectState?.research_report || 'Loading research...')}
              {activeTab === 'techstack' && projectState?.tech_stack && (
                <pre className="text-xs font-mono">
                  {JSON.stringify(JSON.parse(projectState.tech_stack), null, 2)}
                </pre>
              )}
              {activeTab === 'techstack' && !projectState?.tech_stack && 'Loading tech stack...'}
            </ScrollableOutput>

            {activeTab === 'techstack' && projectState?.tech_stack && (() => {
              try {
                const stack = JSON.parse(projectState.tech_stack)
                return (
                  <div className="flex flex-wrap gap-2 mt-3">
                    {[stack.frontend, stack.backend, stack.database, stack.auth].map((item, i) => (
                      <span key={i} className="bg-blue-100 text-blue-700 px-2 py-1 rounded text-xs font-medium">
                        {item}
                      </span>
                    ))}
                  </div>
                )
              } catch { return null }
            })()}
          </div>
        )}

        {gateName === 'human_gate_2' && projectState?.architecture_doc && (
          <div className="mt-4 space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-center">
                <div className="text-lg font-bold text-blue-700">
                  {projectState.file_list?.length || 0}
                </div>
                <div className="text-xs text-blue-600">Files to Generate</div>
              </div>
              <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-center">
                <div className="text-lg font-bold text-green-700">{apiEndpointCount}</div>
                <div className="text-xs text-green-600">API Endpoints</div>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              {[
                { id: 'architecture', label: 'Architecture Doc' },
                { id: 'diagrams', label: `Diagrams (${mermaidBlocks.length})` },
                { id: 'filelist', label: `Files (${projectState.file_list?.length || 0})` }
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`rounded-full px-3 py-1 text-sm font-medium transition-colors ${
                    activeTab === tab.id
                      ? 'bg-green-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {activeTab === 'architecture' && (
              <ScrollableOutput>
                <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-gray-700">
                  {projectState.architecture_doc}
                </pre>
              </ScrollableOutput>
            )}

            {activeTab === 'diagrams' && (
              <div className="max-h-96 space-y-4 overflow-y-auto">
                {mermaidBlocks.length === 0 ? (
                  <p className="text-sm text-gray-500">No Mermaid diagrams found in architecture output.</p>
                ) : (
                  mermaidBlocks.map((code, index) => (
                    <MermaidDiagram
                      key={`${index}-${code.slice(0, 24)}`}
                      code={code}
                      title={code.startsWith('erDiagram') ? 'Entity Relationship Diagram' : `Data Flow Diagram ${index + 1}`}
                    />
                  ))
                )}
              </div>
            )}

            {activeTab === 'filelist' && (
              <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <p className="mb-3 text-xs text-gray-500">
                  These {projectState.file_list?.length || 0} files will be generated by the coder agents after you approve the implementation plan.
                </p>
                <div className="max-h-[32rem] overflow-auto">
                  {(projectState.file_list || []).map((file, index) => {
                    const isBackend = file.includes('backend') || file.endsWith('.py')
                    const isFrontend = file.includes('frontend') || file.includes('src/') || file.endsWith('.jsx') || file.endsWith('.tsx')
                    const isConfig = file.endsWith('.yml') || file.endsWith('.yaml') || file.endsWith('.json') || file === 'Dockerfile' || file.endsWith('.md')
                    const color = isBackend ? 'text-blue-600' : isFrontend ? 'text-green-600' : isConfig ? 'text-orange-600' : 'text-gray-600'

                    return (
                      <div key={`${file}-${index}`} className={`py-0.5 font-mono text-xs ${color}`}>
                        {file}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {renderFileBrowser()}

        {submitting ? (
          <div className="text-sm text-blue-600 font-medium italic animate-pulse">
            Pipeline resuming...
          </div>
        ) : (
          <div className="space-y-4 pt-2">
            <div className="flex space-x-3">
              <button
                onClick={handleApprove}
                disabled={isPending}
                className="flex-1 bg-green-600 hover:bg-green-700 text-white font-semibold py-2 px-3 rounded text-sm transition-colors cursor-pointer text-center"
              >
                {submitting === 'approve' ? 'Sending...' : '✅ Approve & Continue'}
              </button>
              <button
                onClick={() => setFeedbackOpen(!feedbackOpen)}
                disabled={isPending}
                className="flex-1 bg-white hover:bg-gray-50 text-gray-700 border border-gray-300 font-semibold py-2 px-3 rounded text-sm transition-colors cursor-pointer text-center"
              >
                ✏️ Request Changes
              </button>
            </div>

            {feedbackOpen && (
              <div className="space-y-3 pt-2 border-t border-gray-100">
                <label className="block text-xs font-semibold text-gray-600">
                  Feedback / Request details:
                </label>
                <textarea
                  value={feedbackText}
                  onChange={(e) => setFeedbackText(e.target.value)}
                  placeholder="Tell the agents what to change or refine..."
                  rows={4}
                  className="w-full px-3 py-2 border border-gray-300 rounded text-sm text-gray-900 focus:ring-1 focus:ring-blue-500 focus:outline-none placeholder-gray-400"
                />
                <button
                  onClick={handleSubmitChanges}
                  disabled={isPending || !feedbackText.trim()}
                  className={`w-full py-2 px-4 rounded text-white font-semibold text-sm transition-colors cursor-pointer text-center ${
                    !feedbackText.trim()
                      ? 'bg-blue-300 cursor-not-allowed'
                      : 'bg-blue-600 hover:bg-blue-700'
                  }`}
                >
                  {submitting === 'edit' ? 'Sending...' : 'Submit Changes'}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  // Default: Pipeline running state
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6 space-y-4">
      <div className="flex items-center space-x-2">
        <span className="h-2.5 w-2.5 rounded-full bg-blue-500 animate-pulse" />
        <h3 className="text-sm font-semibold text-gray-700">Agents working...</h3>
      </div>

      <div className="space-y-2">
        <div className="text-xs text-gray-500">Current Stage:</div>
        <div className="text-base font-bold text-gray-900 capitalize">
          {currentStage || 'Initializing...'}
        </div>
      </div>

      {renderFileBrowser()}

      <div className="border-t border-gray-100 pt-3 flex justify-between items-center text-xs text-gray-500">
        <span>Events Streamed:</span>
        <span className="font-semibold text-gray-700">{eventsCount}</span>
      </div>
    </div>
  )
}
