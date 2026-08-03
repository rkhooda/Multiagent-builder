import { useEffect, useMemo, useState } from 'react'
import { DesktopOnly } from '../ui'
import FileBrowser, { formatBytes } from './FileBrowser'
import QAReportPanel, { parseQAReport } from './QAReportPanel'
import DiffView from './DiffView'
import MetricsPanel from '../MetricsPanel'

const API = '/api/projects'
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
    <div className="rounded border border-line bg-overlay px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-3">{label}</div>
      <div className="mt-0.5 text-sm font-bold text-ink">{children}</div>
    </div>
  )
}

/**
 * Day 22 quality threshold banner. WARNS, never blocks — download stays
 * enabled and the decision stays human. The rate is computed server-side as
 * (files with unresolved mechanical issues) / (files attempted), each file
 * counted once no matter how many problems it has.
 */
function QualityThresholdBanner({ report }) {
  const [showBreakdown, setShowBreakdown] = useState(false)
  if (!report || !report.below_threshold) return null

  const pct = Math.round((report.failure_rate || 0) * 100)
  const thresholdPct = Math.round((report.quality_threshold || 0.2) * 100)
  const rows = [
    ['Syntax errors (unrepaired)', report.syntax_errors_found],
    ['Phantom imports', report.phantom_imports],
    ['Missing packages', report.missing_packages],
    ['Invalid JSON/YAML', report.artifact_errors],
    ['Page composition warnings', report.coherence_warnings],
    ['Failed/blocked generation', report.generation_failed],
  ].filter(([, count]) => count > 0)

  return (
    <div className="rounded-lg border border-warn/45 bg-warn/10 p-3">
      <div className="flex items-start gap-2">
        <span aria-hidden="true" className="text-base leading-none">⚠</span>
        <div className="flex-1">
          <p className="text-sm font-semibold text-warn">
            Code quality below threshold: {pct}% of files have unresolved issues.
          </p>
          <p className="mt-0.5 text-xs text-warn">
            Consider reviewing the architecture or replanning before downloading.
            {' '}(Threshold: {thresholdPct}%. Download remains available.)
          </p>
          {report.repair_budget_exhausted && (
            <p className="mt-1 text-xs font-medium text-warn">
              Repair budget exhausted ({report.repair_calls_spent}/{report.repair_ceiling} calls).
              A run needing this many repairs usually points at the prompts or architecture.
            </p>
          )}
          {(report.degraded_tools || []).map((tool) => (
            <p key={tool} className="mt-1 text-xs text-warn">Reduced checking: {tool}</p>
          ))}
          <button
            type="button"
            onClick={() => setShowBreakdown((open) => !open)}
            aria-expanded={showBreakdown}
            className="mt-1.5 text-xs font-semibold text-warn underline hover:brightness-110"
          >
            {showBreakdown ? 'Hide breakdown' : 'Show breakdown'}
          </button>
          {showBreakdown && (
            <div className="mt-2 rounded border border-warn/35 bg-raised p-2">
              <table className="w-full text-xs">
                <tbody>
                  {rows.map(([label, count]) => (
                    <tr key={label}>
                      <td className="py-0.5 pr-3 text-ink">{label}</td>
                      <td className="py-0.5 text-right font-semibold text-ink">{count}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-warn/35">
                    <td className="py-0.5 pr-3 font-medium text-ink">
                      Files affected / checked
                    </td>
                    <td className="py-0.5 text-right font-bold text-ink">
                      {(report.unresolved_files || []).length} / {report.files_checked}
                    </td>
                  </tr>
                </tbody>
              </table>
              {(report.unresolved_files || []).length > 0 && (
                <ul className="mt-1.5 max-h-32 space-y-0.5 overflow-y-auto font-mono text-[10px] text-ink-2">
                  {report.unresolved_files.map((path) => <li key={path}>{path}</li>)}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// Human-readable labels for degraded_events keys; unknown keys render raw so a
// new backend event is never invisible while waiting for a label here.
const DEGRADED_LABELS = {
  qa_batch_failed: 'QA batch failed (files in it went unreviewed by the model)',
  qa_stream_fallback: 'QA stream fell back to end-of-run review',
  qa_stream_stalled: 'QA stream stalled',
  qa_rereview_capped: 'File changed after review, re-review withheld (cap)',
  validation_js_unavailable: 'JS deep validation unavailable — heuristic only',
  review_failed_open: 'Reviewer failed open (file treated as pass)',
  revision_failed_open: 'Revision call failed (original kept)',
  revision_withheld_budget: 'Revision withheld (repair budget)',
}

function degradedLabel(key) {
  if (DEGRADED_LABELS[key]) return DEGRADED_LABELS[key]
  if (key.startsWith('llm_truncated:')) return `Output truncated at token ceiling (${key.split(':')[1]})`
  if (key.startsWith('agent_skipped:')) return `Stage skipped after failure (${key.split(':')[1]})`
  return key
}

/**
 * Improvement 02 fail-open accounting: every place the pipeline silently
 * continued in a reduced mode, counted and shown. WARNS, never blocks —
 * same stance as the quality threshold banner.
 */
function DegradedEventsBanner({ events }) {
  const entries = Object.entries(events || {}).filter(([, n]) => n > 0)
  if (entries.length === 0) return null
  const total = entries.reduce((a, [, n]) => a + n, 0)
  return (
    <div className="rounded-lg border border-warn/45 bg-warn/10 p-3">
      <div className="flex items-start gap-2">
        <span aria-hidden="true" className="text-base leading-none">⚠</span>
        <div className="flex-1">
          <p className="text-sm font-semibold text-warn">
            {total} degraded event{total === 1 ? '' : 's'}: parts of this run continued in a
            reduced mode.
          </p>
          <ul className="mt-1 space-y-0.5 text-xs text-warn">
            {entries.map(([key, count]) => (
              <li key={key}>
                {degradedLabel(key)}
                {count > 1 && <span className="ml-1 font-semibold">×{count}</span>}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}

function SummaryCard({ filesData, projectState, severityCounts }) {
  const totalIssues = severityCounts.critical + severityCounts.warnings + severityCounts.info
  const models = [...new Set(Object.values(projectState?.agent_models || {}))]

  return (
    <div className="rounded-lg border border-line bg-raised p-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Files Generated">{filesData ? filesData.total_files : '…'}</Stat>
        <Stat label="Lines of Code">{filesData ? filesData.total_lines.toLocaleString() : '…'}</Stat>
        <Stat label="QA Issues">
          {projectState?.qa_issues_count === -1 ? (
            <span className="text-warn">Not reviewed</span>
          ) : (
            <>
              {totalIssues || projectState?.qa_issues_count || 0}
              <span className="ml-1.5 text-[10px] font-semibold text-ink-3">
                {severityCounts.critical > 0 && <span className="text-err">{severityCounts.critical} crit </span>}
                {severityCounts.warnings > 0 && <span className="text-accent">{severityCounts.warnings} warn </span>}
                {severityCounts.info > 0 && <span className="text-run">{severityCounts.info} info</span>}
              </span>
            </>
          )}
        </Stat>
        <Stat label="Total Pipeline Time">
          {formatDuration(projectState?.generation_seconds)}
          <span className="ml-1 text-[10px] font-normal text-ink-3">incl. reviews</span>
        </Stat>
        {/* The old "Token Cost $0.00" stat moved to MetricsPanel below: on free
            tiers the dollar figure is always zero and says nothing, while the
            token counts it was standing in for are the real budget. */}
      </div>
      {models.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-3">Models used:</span>
          {models.map((model) => (
            <span key={model} className="rounded bg-overlay px-2 py-0.5 font-mono text-[10px] text-ink-2">
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
      <div className="w-full max-w-lg space-y-3 rounded-lg bg-raised p-5 shadow-xl">
        <h4 className="text-sm font-bold text-ink">Request AI Fix</h4>
        <p className="break-all font-mono text-xs text-ink-2">{filepath}</p>

        {dependentCount > 0 && (
          <div className="rounded border border-accent/35 bg-accent-soft px-3 py-2 text-xs font-medium text-accent">
            ⚠ {dependentCount} file{dependentCount > 1 ? 's' : ''} import{dependentCount > 1 ? '' : 's'} this one —
            review {dependentCount > 1 ? 'them' : 'it'} after the fix. Dependents are not regenerated automatically.
          </div>
        )}

        <label className="block text-xs font-semibold text-ink-2">
          What needs to be fixed in this file?
        </label>
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          rows={4}
          autoFocus
          placeholder="Describe the fix, or click a QA finding below to include it…"
          className="w-full rounded border border-line-strong px-3 py-2 text-sm text-ink placeholder-ink-3 focus:outline-none focus:ring-1 focus:ring-accent"
        />

        {findings.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-3">
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
                  className="max-w-full truncate rounded-full border border-accent/35 bg-accent-soft px-2.5 py-1 text-left text-[11px] text-accent hover:bg-accent/15"
                >
                  {finding.description.slice(0, 80)}
                  {finding.description.length > 80 ? '…' : ''}
                </button>
              ))}
            </div>
          </div>
        )}

        {error && <p className="text-xs font-medium text-err">{error}</p>}

        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={() => onSubmit(instruction)}
            disabled={submitting || instruction.trim().length < 5}
            className="w-full rounded bg-overlay py-2 px-3 text-sm font-semibold text-ink hover:bg-line disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? 'Fixing… this can take a minute' : 'Fix This File'}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-line-strong bg-raised py-2 px-3 text-sm font-semibold text-ink hover:bg-overlay"
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

  // Improvement 01: reviewer outcome per file. Keys are already real filepaths
  // (the coder wrote them), so unlike QA findings these need no path resolution.
  const reviewByFile = useMemo(
    () => new Map(Object.entries(projectState?.review_results || {})),
    [projectState?.review_results],
  )
  const reviewStats = useMemo(() => {
    let reviewed = 0, revised = 0
    reviewByFile.forEach((r) => {
      if (r?.reviewed) reviewed += 1
      if (r?.revised) revised += 1
    })
    return { reviewed, revised }
  }, [reviewByFile])

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
    <div className="space-y-4 lg:grid lg:grid-cols-[minmax(0,1fr)_340px] lg:items-start lg:gap-5 lg:space-y-0">
      <div className="min-w-0 space-y-4">
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-accent" />
        <h3 className="text-base font-bold uppercase tracking-wide text-ink">
          Gate 4 — Final Review
        </h3>
        <button
          type="button"
          onClick={handleDownload}
          className="ml-auto rounded bg-overlay px-4 py-2 text-sm font-semibold text-ink hover:bg-line"
        >
          ⬇ Download Project ZIP
        </button>
      </div>

      {downloadToast && (
        <div className="rounded border border-run/35 bg-run/10 px-3 py-2 text-xs font-medium text-run">
          Preparing ZIP… your browser will save{' '}
          {filesData ? `${filesData.total_files} files (${formatBytes(filesData.total_bytes)})` : 'the project'} shortly.
        </div>
      )}

      <QualityThresholdBanner report={projectState?.validation_report} />
      <DegradedEventsBanner events={projectState?.degraded_events} />

      <SummaryCard filesData={filesData} projectState={projectState} severityCounts={severityCounts} />

      <MetricsPanel projectId={projectId} />

      {/* Partial-generation warning — reload-safe: derived from stage_history's
          per-stage failed_files (which the coder records as failed + blocked),
          not from the live event stream that may be gone after a reload. */}
      {(() => {
        const notGenerated = [...new Set(
          (projectState?.stage_history || [])
            .flatMap((h) => h.failed_files || [])
        )]
        if (notGenerated.length === 0) return null
        return (
          <div className="rounded-lg border border-warn/35 bg-warn/10 px-4 py-2.5 text-xs font-medium text-warn">
            ⚠️ {notGenerated.length} file{notGenerated.length > 1 ? 's' : ''} did not generate
            (failed or blocked by a failed dependency) and {notGenerated.length > 1 ? 'were' : 'was'} written
            as a placeholder — review the QA report and use Request AI Fix to regenerate:{' '}
            <span className="font-mono text-warn">{notGenerated.slice(0, 4).join(', ')}{notGenerated.length > 4 ? ` +${notGenerated.length - 4} more` : ''}</span>
          </div>
        )
      })()}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-line">
        {[
          { key: 'files', label: `Files${filesData ? ` (${filesData.total_files})` : ''}` },
          {
            key: 'qa',
            label: projectState?.qa_issues_count === -1
              ? 'QA Report (skipped)'
              : `QA Report${projectState?.qa_issues_count ? ` (${projectState.qa_issues_count})` : ''}`,
          },
        ].map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={`rounded-t border-b-2 px-4 py-2 text-sm font-semibold ${
              activeTab === tab.key
                ? 'border-accent text-run'
                : 'border-transparent text-ink-3 hover:text-ink'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'files' ? (
        filesError ? (
          <p className="text-sm text-err">Failed to load files: {filesError}</p>
        ) : (
          <DesktopOnly label="The file browser">
          <FileBrowser
            projectId={projectId}
            filesData={filesData}
            issueCountByFile={issueCountByFile}
            reviewByFile={reviewByFile}
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
          </DesktopOnly>
        )
      ) : (
        <QAReportPanel qaReport={qaReport} parsedReport={parsedReport} onOpenFile={handleOpenFile} />
      )}

      {/* Final actions */}
      </div>
      <div className="sticky bottom-0 z-10 border-t border-line bg-raised pt-3 lg:top-0 lg:bottom-auto lg:rounded-lg lg:border lg:p-4">
        {confirmingCancel ? (
          <div className="space-y-2 rounded border border-err/35 bg-err/10 p-3">
            <p className="text-sm font-medium text-err">Cancel this project? This cannot be undone.</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleCancelProject}
                disabled={isPending}
                className="rounded bg-err px-3 py-1.5 text-xs font-semibold text-ink hover:brightness-110 disabled:opacity-60"
              >
                {submitting === 'reject' ? 'Cancelling…' : 'Yes, cancel project'}
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
          <div className="flex gap-3">
            <button
              type="button"
              onClick={handleComplete}
              disabled={isPending || fixing}
              className="w-full rounded-md border border-accent bg-accent py-2 px-3 text-sm font-semibold text-accent-ink hover:brightness-110 disabled:opacity-50"
            >
              {submitting === 'approve' ? 'Completing…' : 'Mark project complete'}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={isPending || fixing}
              className="rounded border border-err/35 bg-raised py-2 px-3 text-sm font-semibold text-err hover:bg-err/10 disabled:opacity-60"
            >
              Cancel project
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
