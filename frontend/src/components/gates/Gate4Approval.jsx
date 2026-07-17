import { useEffect, useMemo, useState } from 'react'
import FileBrowser, { formatBytes } from './FileBrowser'
import QAReportPanel, { parseQAReport } from './QAReportPanel'
import DiffView from './DiffView'

const API = 'http://localhost:8000/api/projects'

export default function Gate4Approval({ projectId, projectState, status, onResume }) {
  const [filesData, setFilesData] = useState(null)
  const [filesError, setFilesError] = useState('')
  const [activeTab, setActiveTab] = useState('files')
  const [selectedPath, setSelectedPath] = useState('')
  const [downloadToast, setDownloadToast] = useState(false)

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

  const handleDownload = () => {
    setDownloadToast(true)
    setTimeout(() => setDownloadToast(false), 4000)
    // Plain navigation download — no auth headers needed, browser handles the save.
    window.location.href = `${API}/${projectId}/download`
  }

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
            onRequestFix={() => {}}
            fixDisabledReason="Coming next"
            renderDiff={(oldText, newText) => <DiffView oldText={oldText} newText={newText} />}
          />
        )
      ) : (
        <QAReportPanel qaReport={qaReport} parsedReport={parsedReport} onOpenFile={handleOpenFile} />
      )}
    </div>
  )
}
