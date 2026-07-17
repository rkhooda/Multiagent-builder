import { useEffect, useMemo, useState } from 'react'
import FileBrowser, { formatBytes } from './FileBrowser'
import QAReportPanel, { parseQAReport } from './QAReportPanel'
import DiffView from './DiffView'

const API = 'http://localhost:8000/api/projects'
const MAX_FIXES_PER_FILE = 3

function formatDuration(seconds) {
  if (seconds == null) return 'n/a'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return m > 0 ? `${m}m ${seconds % 60}s` : `${seconds}s`
}

function Stat({ label, children }) {
  return (
    <div className="rounded border border-gray-200 bg-gray-50 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-0.5 text-sm font-bold text-gray-800">{children}</div>
    </div>
  )
}

function SummaryCard({ filesData, projectState, severityCounts }) {
  const totalIssues = severityCounts.critical + severityCounts.warnings + severityCounts.info
  const models = [...new Set(Object.values(projectState?.agent_models || {}))]

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Files Generated">{filesData ? filesData.total_files : '…'}</Stat>
        <Stat label="Lines of Code">{filesData ? filesData.total_lines.toLocaleString() : '…'}</Stat>
        <Stat label="QA Issues">
          {totalIssues || projectState?.qa_issues_count || 0}
          <span className="ml-1.5 text-[10px] font-semibold text-gray-500">
            {severityCounts.critical > 0 && <span className="text-red-600">{severityCounts.critical} crit </span>}
            {severityCounts.warnings > 0 && <span className="text-orange-600">{severityCounts.warnings} warn </span>}
            {severityCounts.info > 0 && <span className="text-blue-600">{severityCounts.info} info</span>}
          </span>
        </Stat>
        <Stat label="Total Pipeline Time">
          {formatDuration(projectState?.generation_seconds)}
          <span className="ml-1 text-[10px] font-normal text-gray-400">incl. reviews</span>
        </Stat>
        <Stat label="Token Cost">$0.00 <span className="text-[10px] font-normal text-gray-400">free tiers</span></Stat>
      </div>
      {models.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">Models used:</span>
          {models.map((model) => (
            <span key={model} className="rounded bg-gray-100 px-2 py-0.5 font-mono text-[10px] text-gray-600">
              {model}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function FixModal({ filepath, findings, dependentCount, submitting, error, onSubmit, onClose }) {
  const [instruction, setInstruction] = useState('')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg space-y-3 rounded-lg bg-white p-5 shadow-xl">
        <h4 className="text-sm font-bold text-gray-900">Request AI Fix</h4>
        <p className="break-all font-mono text-xs text-gray-600">{filepath}</p>

        {dependentCount > 0 && (
          <div className="rounded border border-orange-200 bg-orange-50 px-3 py-2 text-xs font-medium text-orange-700">
            ⚠ {dependentCount} file{dependentCount > 1 ? 's' : ''} import{dependentCount > 1 ? '' : 's'} this one —
            review {dependentCount > 1 ? 'them' : 'it'} after the fix. Dependents are not regenerated automatically.
          </div>
        )}

        <label className="block text-xs font-semibold text-gray-600">
          What needs to be fixed in this file?
        </label>
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          rows={4}
          autoFocus
          placeholder="Describe the fix, or click a QA finding below to include it…"
          className="w-full rounded border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-purple-500"
        />

        {findings.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              QA findings for this file — click to include
            </span>
            <div className="flex flex-wrap gap-1.5">
              {findings.map((finding, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() =>
                    setInstruction((prev) => (prev ? `${prev.trimEnd()}\n- ${finding.description}` : `- ${finding.description}`))
                  }
                  title={finding.description}
                  className="max-w-full truncate rounded-full border border-orange-200 bg-orange-50 px-2.5 py-1 text-left text-[11px] text-orange-800 hover:bg-orange-100"
                >
                  {finding.description.slice(0, 80)}
                  {finding.description.length > 80 ? '…' : ''}
                </button>
              ))}
            </div>
          </div>
        )}

        {error && <p className="text-xs font-medium text-red-600">{error}</p>}

        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={() => onSubmit(instruction)}
            disabled={submitting || instruction.trim().length < 5}
            className="flex-1 rounded bg-purple-600 py-2 px-3 text-sm font-semibold text-white hover:bg-purple-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? 'Fixing… this can take a minute' : 'Fix This File'}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-gray-300 bg-white py-2 px-3 text-sm font-semibold text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Gate4Approval({ projectId, projectState, status, onResume }) {
  const [submitting, setSubmitting] = useState(null)
  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [filesData, setFilesData] = useState(null)
  const [filesError, setFilesError] = useState('')
  const [activeTab, setActiveTab] = useState('files')
  const [selectedPath, setSelectedPath] = useState('')
  const [downloadToast, setDownloadToast] = useState(false)
  const [fixModalPath, setFixModalPath] = useState('')
  const [fixing, setFixing] = useState(false)
  const [fixError, setFixError] = useState('')
  const [fixCounts, setFixCounts] = useState(() => ({ ...(projectState?.fix_counts || {}) }))
  const [contentVersion, setContentVersion] = useState({})
  // Seed pre-fix snapshots from persisted state so diffs survive a reload;
  // previous_versions also holds doc snapshots, so keep only real file paths.
  const [previousContent, setPreviousContent] = useState(() => {
    const snapshots = projectState?.previous_versions || {}
    const fileKeys = Object.keys(projectState?.generated_files || {})
    return Object.fromEntries(Object.entries(snapshots).filter(([key]) => fileKeys.includes(key)))
  })

  const qaReport = projectState?.qa_report || ''
  const parsedReport = useMemo(() => parseQAReport(qaReport), [qaReport])

  // QA findings reference paths as the agents wrote them — resolve loosely
  // against the real file list so a clicked finding still opens its file.
  const resolveFilePath = (findingPath) => {
    const files = filesData?.files || []
    if (files.some((f) => f.path === findingPath)) return findingPath
    const match = files.find((f) => f.path.endsWith(findingPath) || findingPath.endsWith(f.path))
    return match?.path || ''
  }

  // Re-key QA issue counts by resolved disk path so tree warning dots line up.
  const issueCountByFile = useMemo(() => {
    const map = new Map()
    parsedReport.issueCountByFile.forEach((count, path) => {
      const resolved = resolveFilePath(path)
      if (resolved) map.set(resolved, (map.get(resolved) || 0) + count)
    })
    return map
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parsedReport, filesData])

  const handleOpenFile = (findingPath) => {
    const resolved = resolveFilePath(findingPath)
    if (!resolved) return
    setSelectedPath(resolved)
    setActiveTab('files')
  }

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/${projectId}/files`)
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.json())?.detail || `HTTP ${res.status}`)
        return res.json()
      })
      .then((data) => {
        if (cancelled) return
        setFilesData(data)
        if (data.files?.length) setSelectedPath((prev) => prev || data.files[0].path)
      })
      .catch((err) => {
        if (!cancelled) setFilesError(String(err.message || err))
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  const findingsForPath = (path) =>
    Object.values(parsedReport.sections)
      .flatMap((section) => section.findings)
      .filter((finding) => resolveFilePath(finding.file) === path)

  const fixModalDependentCount = useMemo(() => {
    if (!fixModalPath) return 0
    try {
      const tasks = JSON.parse(projectState?.implementation_plan || '[]')
      const task = tasks.find((t) => t.filepath === fixModalPath)
      if (!task) return 0
      return tasks.filter((t) => (t.requires || []).includes(task.id)).length
    } catch {
      return 0
    }
  }, [fixModalPath, projectState])

  const selectedFixCount = fixCounts[selectedPath] || 0
  const fixDisabledReason = fixing
    ? 'A fix is already in progress'
    : selectedFixCount >= MAX_FIXES_PER_FILE
      ? `Fix limit reached (${MAX_FIXES_PER_FILE} per file) — edit manually after download`
      : ''

  const handleFixSubmit = async (instruction) => {
    setFixing(true)
    setFixError('')
    try {
      const res = await fetch(`${API}/${projectId}/files/fix`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filepath: fixModalPath, instruction }),
      })
      const data = await res.json()
      if (!res.ok) {
        setFixError(typeof data?.detail === 'string' ? data.detail : 'Fix request failed')
        return
      }
      setPreviousContent((prev) => ({ ...prev, [data.filepath]: data.previous_content }))
      setContentVersion((prev) => ({ ...prev, [data.filepath]: (prev[data.filepath] || 0) + 1 }))
      setFixCounts((prev) => ({ ...prev, [data.filepath]: data.fix_count }))
      setFixModalPath('')
      setSelectedPath(data.filepath)
      setActiveTab('files')
    } catch (err) {
      setFixError(String(err.message || err))
    } finally {
      setFixing(false)
    }
  }

  const handleDownload = () => {
    setDownloadToast(true)
    setTimeout(() => setDownloadToast(false), 4000)
    // Plain navigation download — no auth headers needed, browser handles the save.
    window.location.href = `${API}/${projectId}/download`
  }

  const handleComplete = async () => {
    setSubmitting('approve')
    try {
      await onResume('approve', '')
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

  const severityCounts = {
    critical: parsedReport.sections.Critical?.findings.length || 0,
    warnings: parsedReport.sections.Warnings?.findings.length || 0,
    info: parsedReport.sections.Info?.findings.length || 0,
  }
  const isPending = submitting !== null || status !== 'awaiting_approval'

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-orange-500" />
        <h3 className="text-base font-bold uppercase tracking-wide text-gray-900">
          Gate 4 — Final Review
        </h3>
        <button
          type="button"
          onClick={handleDownload}
          className="ml-auto rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
        >
          ⬇ Download Project ZIP
        </button>
      </div>

      {downloadToast && (
        <div className="rounded border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-medium text-blue-700">
          Preparing ZIP… your browser will save{' '}
          {filesData ? `${filesData.total_files} files (${formatBytes(filesData.total_bytes)})` : 'the project'} shortly.
        </div>
      )}

      <SummaryCard filesData={filesData} projectState={projectState} severityCounts={severityCounts} />

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {[
          { key: 'files', label: `Files${filesData ? ` (${filesData.total_files})` : ''}` },
          { key: 'qa', label: `QA Report${projectState?.qa_issues_count ? ` (${projectState.qa_issues_count})` : ''}` },
        ].map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={`rounded-t border-b-2 px-4 py-2 text-sm font-semibold ${
              activeTab === tab.key
                ? 'border-blue-600 text-blue-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'files' ? (
        filesError ? (
          <p className="text-sm text-red-600">Failed to load files: {filesError}</p>
        ) : (
          <FileBrowser
            projectId={projectId}
            filesData={filesData}
            issueCountByFile={issueCountByFile}
            selectedPath={selectedPath}
            onSelectPath={setSelectedPath}
            onRequestFix={(path) => {
              setFixError('')
              setFixModalPath(path)
            }}
            fixDisabledReason={fixDisabledReason}
            contentVersion={contentVersion}
            previousContent={previousContent}
            renderDiff={(oldText, newText) => <DiffView oldText={oldText} newText={newText} />}
          />
        )
      ) : (
        <QAReportPanel qaReport={qaReport} parsedReport={parsedReport} onOpenFile={handleOpenFile} />
      )}

      {/* Final actions */}
      <div className="sticky bottom-0 border-t border-gray-100 bg-white pt-3">
        {confirmingCancel ? (
          <div className="space-y-2 rounded border border-red-200 bg-red-50 p-3">
            <p className="text-sm font-medium text-red-700">Cancel this project? This cannot be undone.</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleCancelProject}
                disabled={isPending}
                className="rounded bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
              >
                {submitting === 'reject' ? 'Cancelling…' : 'Yes, cancel project'}
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
              onClick={handleComplete}
              disabled={isPending || fixing}
              className="flex-1 rounded bg-green-600 py-2 px-3 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-60"
            >
              {submitting === 'approve' ? 'Completing…' : '✅ Mark Project Complete'}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={isPending || fixing}
              className="rounded border border-red-200 bg-white py-2 px-3 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-60"
            >
              Cancel Project
            </button>
          </div>
        )}
      </div>

      {fixModalPath && (
        <FixModal
          filepath={fixModalPath}
          findings={findingsForPath(fixModalPath)}
          dependentCount={fixModalDependentCount}
          submitting={fixing}
          error={fixError}
          onSubmit={handleFixSubmit}
          onClose={() => setFixModalPath('')}
        />
      )}
    </div>
  )
}
