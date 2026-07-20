import { useEffect, useState } from 'react'

const API = '/api/projects'

function formatBytes(n) {
  if (!n) return '0 B'
  return n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`
}

function formatDuration(seconds) {
  if (!seconds) return '—'
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`
}

function Stat({ label, value, hint }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-3">{label}</p>
      <p className="truncate text-lg font-bold text-ink">{value}</p>
      {hint && <p className="truncate text-[10px] text-ink-3">{hint}</p>}
    </div>
  )
}

/**
 * The project's permanent record: stats, file list and exports, rendered
 * entirely from persisted state and on-disk files. Nothing here depends on
 * having watched the run live, so it is identical on a cold load a week later
 * as it is the moment the pipeline finishes.
 */
export default function ProjectRecord({ projectId, projectState }) {
  const [files, setFiles] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    // Both are optional: a project that never generated files 404s on /files,
    // and a pre-Day-23 project has no metrics. Neither is an error here.
    fetch(`${API}/${projectId}/files`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => !cancelled && setFiles(data))
      .catch(() => {})
    fetch(`${API}/${projectId}/metrics`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => !cancelled && setMetrics(data))
      .catch(() => {})
    return () => { cancelled = true }
  }, [projectId])

  const qaIssues = projectState?.qa_issues_count
  const fileList = files?.files || []
  const shown = expanded ? fileList : fileList.slice(0, 8)

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-line bg-raised p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="grid flex-1 grid-cols-2 gap-4 sm:grid-cols-5">
            <Stat label="Files" value={files?.total_files ?? '—'} hint={files ? formatBytes(files.total_bytes) : ''} />
            <Stat label="Lines of code" value={files?.total_lines?.toLocaleString() ?? '—'} />
            <Stat
              label="QA issues"
              value={qaIssues === -1 ? 'Not reviewed' : (qaIssues ?? 0)}
              hint={qaIssues === -1 ? 'QA was skipped' : ''}
            />
            <Stat label="Duration" value={formatDuration(projectState?.generation_seconds)} hint="incl. review time" />
            <Stat
              label="Tokens"
              value={metrics?.has_metrics ? metrics.total_tokens.toLocaleString() : '—'}
              hint={metrics?.has_metrics ? `${metrics.ok_attempts} calls` : 'no metrics recorded'}
            />
          </div>
          <div className="flex flex-shrink-0 flex-col gap-1.5">
            <a
              href={`${API}/${projectId}/download`}
              className="whitespace-nowrap rounded bg-overlay px-3 py-1.5 text-xs font-semibold text-ink hover:bg-overlay"
            >
              ↓ Download ZIP
            </a>
            <a
              href={`${API}/${projectId}/summary.pdf`}
              className="whitespace-nowrap rounded border border-line-strong px-3 py-1.5 text-center text-xs font-semibold text-ink hover:bg-overlay"
            >
              Export Summary
            </a>
          </div>
        </div>
      </div>

      {fileList.length > 0 && (
        <div className="rounded-lg border border-line bg-raised">
          <div className="border-b border-line px-4 py-2">
            <h4 className="text-xs font-bold uppercase tracking-wide text-ink-3">
              Generated Files ({fileList.length})
            </h4>
          </div>
          <ul className="divide-y divide-line">
            {shown.map((file) => (
              <li key={file.path} className="flex items-center justify-between px-4 py-1.5">
                <span className="truncate font-mono text-xs text-ink">{file.path}</span>
                <span className="ml-3 flex-shrink-0 font-mono text-[10px] text-ink-3">
                  {file.line_count ?? '—'} lines · {formatBytes(file.size_bytes)}
                </span>
              </li>
            ))}
          </ul>
          {fileList.length > 8 && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="w-full border-t border-line py-2 text-xs font-semibold text-run hover:bg-overlay"
            >
              {expanded ? 'Show less' : `Show all ${fileList.length} files`}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
