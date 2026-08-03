import React, { useEffect, useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useProjectStream } from '../hooks/useProjectStream'
import ApprovalGate from '../components/ApprovalGate'
import StageTimeline from '../components/StageTimeline'
import MetricsPanel from '../components/MetricsPanel'
import Gate1Approval from '../components/gates/Gate1Approval'
import Gate2Approval from '../components/gates/Gate2Approval'
import Gate3Approval from '../components/gates/Gate3Approval'
import Gate4Approval from '../components/gates/Gate4Approval'
import ErrorCard from '../components/ErrorCard'
import PhaseProgress, { derivePhaseProgress, deriveQaStream, QaStreamIndicator } from '../components/PhaseProgress'
import ProjectRecord from '../components/ProjectRecord'
import { RestartDialog, DeleteDialog } from '../components/LifecycleDialogs'
import { statusMeta, stageLabel } from '../lib/status'
import { Badge, Button, Card, Dot, Eyebrow, Skeleton, SkeletonText } from '../components/ui'

function formatTime(isoString) {
  try {
    return new Date(isoString).toLocaleTimeString([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch {
    return isoString
  }
}

const MARKDOWN_AGENTS = new Set(['research', 'requirements', 'architecture', 'qa', 'planning'])

function EventOutput({ event, defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const fullContent = event.content || event.preview || event.output_preview || event.message || ''
  const shortPreview = (event.preview || event.output_preview || fullContent).slice(0, 160)
  const isMarkdown = MARKDOWN_AGENTS.has(event.agent)

  if (!fullContent) return null

  return (
    <div className="my-2 overflow-hidden rounded border border-line bg-overlay/60">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 border-b border-line px-3 py-2 text-left transition-colors hover:bg-overlay"
      >
        <span className="eyebrow">Output</span>
        <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-accent">
          {expanded ? 'Collapse' : 'Expand'}
        </span>
      </button>

      {expanded ? (
        <div className="max-h-96 overflow-auto p-3">
          {isMarkdown ? (
            <div className="markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{fullContent}</ReactMarkdown>
            </div>
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-ink-2">
              {fullContent}
            </pre>
          )}
        </div>
      ) : (
        <div className="p-3">
          <p className="line-clamp-2 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink-3">
            {shortPreview}
          </p>
        </div>
      )}
    </div>
  )
}

/**
 * One event in the live stream.
 *
 * Entry animation comes from a CSS class, so it fires when the browser creates
 * the node and never again. Events already on screen do not re-animate when a
 * new one arrives, which is what keeps a 47-file coder phase from strobing.
 */
const EVENT_TONES = {
  agent_complete: 'ok',
  file_written: 'ok',
  file_failed: 'err',
  error: 'err',
  agent_error: 'err',
  file_blocked: 'idle',
  gate_reached: 'accent',
  validation_complete: 'alt',
  file_reviewed: 'alt',
  file_revised: 'run',
}

const TONE_TEXT = {
  ok: 'text-ok', err: 'text-err', idle: 'text-ink-3',
  accent: 'text-accent', alt: 'text-alt', run: 'text-run',
}

function FeedEvent({ event }) {
  const tone = EVENT_TONES[event.type] || 'run'

  const node = (
    <span className="absolute left-0 top-2 grid h-3 w-3 place-items-center rounded-full border-2 border-raised bg-raised">
      <Dot tone={tone} />
    </span>
  )

  // Per-file events: one line each, deliberately lighter than an agent card.
  // Dozens arrive per phase, so they are a ticker, not a stack of cards.
  if (event.type === 'file_written' || event.type === 'file_failed' || event.type === 'file_blocked') {
    const mark = event.type === 'file_written' ? '✓' : event.type === 'file_failed' ? '✕' : '⊘'
    return (
      <div className="enter relative flex items-start gap-3 pl-7">
        {node}
        <div className="flex min-w-0 flex-1 items-center gap-2 py-0.5">
          <span className={`text-[11px] ${TONE_TEXT[tone]}`}>{mark}</span>
          <span className="truncate font-mono text-[11px] text-ink-2">{event.filepath}</span>
          {event.total != null ? (
            <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-ink-3">
              {event.done}/{event.total}
            </span>
          ) : null}
        </div>
      </div>
    )
  }

  // Reviewer events reuse the per-file ticker line rather than adding a card:
  // they arrive interleaved with file_written during the same phase, and a card
  // per review would bury the generation feed it is annotating.
  if (event.type === 'file_reviewed' || event.type === 'file_revised') {
    const revised = event.type === 'file_revised'
    const label = revised
      ? `revised — ${event.issues_addressed} issue${event.issues_addressed === 1 ? '' : 's'} addressed`
      : event.verdict === 'revise'
        ? `reviewed — ${event.issues_found} issue${event.issues_found === 1 ? '' : 's'}`
        : 'reviewed — pass'
    return (
      <div className="enter relative flex items-start gap-3 pl-7">
        {node}
        <div className="flex min-w-0 flex-1 items-center gap-2 py-0.5">
          <span className={`text-[11px] ${TONE_TEXT[tone]}`}>{revised ? '↻' : '👁'}</span>
          <span className="truncate font-mono text-[11px] text-ink-2">{event.filepath}</span>
          <span className="ml-auto shrink-0 font-mono text-[10px] text-ink-3">{label}</span>
        </div>
      </div>
    )
  }

  if (event.type === 'validation_complete') {
    const below = event.below_threshold
    return (
      <div className="enter relative flex items-start gap-3 pl-7">
        {node}
        <Card className="min-w-0 flex-1 p-3">
          <div className="flex items-center gap-2">
            <Eyebrow>Automated checks</Eyebrow>
            {below && <Badge tone="warn">Below threshold</Badge>}
            <span className="ml-auto font-mono text-[10px] text-ink-3">
              {event.files_checked} files
            </span>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-ink-2">
            <span>{event.syntax_errors} unresolved</span>
            <span>{event.auto_repaired} repaired</span>
            <span>{event.import_warnings || 0} import warnings</span>
            {event.artifact_errors > 0 && <span>{event.artifact_errors} invalid JSON/YAML</span>}
            {event.coherence_warnings > 0 && (
              <span className="text-warn">{event.coherence_warnings} composition</span>
            )}
            <span className="text-ink-3">
              repairs {event.repair_calls_spent}/{event.repair_ceiling}
            </span>
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div className="enter relative flex items-start gap-3 pl-7">
      {node}
      <Card className="min-w-0 flex-1 p-3">
        <div className="flex items-start justify-between gap-2">
          <h4 className="text-[13px] font-semibold capitalize text-ink">
            {event.agent ? event.agent.replace(/_/g, ' ') : 'System'}
          </h4>
          <Badge tone={tone}>{event.type.replace(/_/g, ' ')}</Badge>
        </div>

        {(event.content || event.preview || event.output_preview || event.message) && (
          <EventOutput event={event} />
        )}

        <div className="mt-1.5 font-mono text-[10px] text-ink-3">{formatTime(event.timestamp)}</div>
      </Card>
    </div>
  )
}

export default function ProjectDetailPage() {
  const { projectId } = useParams()
  
  const [projectMetadata, setProjectMetadata] = useState(null)
  const [metadataLoading, setMetadataLoading] = useState(true)
  const [metadataError, setMetadataError] = useState('')
  // Which gate (if any) is currently re-running with human feedback. While set,
  // the gate card stays mounted showing its regenerating overlay instead of
  // being swapped out for the live feed.
  const [regeneratingGate, setRegeneratingGate] = useState(null)
  // {target, startedAt} of the in-flight feedback cycle — lets the timeline
  // know which stage is regenerating right now.
  const [cycleInfo, setCycleInfo] = useState(null)
  const [restarting, setRestarting] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [resuming, setResuming] = useState(false)

  const { events, setEvents, status, setStatus, resumePipeline } = useProjectStream(projectId)
  const bottomRef = useRef(null)

  // Count files written so far
  const fileCount = events.filter(e => e.type === 'file_written').length
  // Per-phase code-generation progress (derived from count-snapshot events).
  const phaseProgress = derivePhaseProgress(events)

  // Fetch project metadata
  const fetchMetadata = async () => {
    try {
      setMetadataLoading(true)
      const res = await fetch(`/api/projects/${projectId}`)
      if (!res.ok) {
        throw new Error('Project not found')
      }
      const data = await res.json()
      setProjectMetadata(data)
      
      // If we have existing logs in the project details, pre-populate the events list
      // so the page doesn't look empty when reloaded. Log lines look like
      // "planning_agent: started ..." / "planning_agent: validation failed ..." /
      // "planning_agent: completed - 47 tasks ...". Keep only the LAST line per
      // agent (its final outcome) so reloading doesn't show a "started"/"retrying"/
      // "completed" card stack for every agent — one clean card per agent instead.
      if (data.log && data.log.length > 0 && events.length === 0) {
        const lastLineByAgent = new Map()
        const agentOrder = []
        data.log.forEach((logStr) => {
          const match = logStr.match(/^([a-zA-Z0-9]+?)(?:_agent)?:\s*/)
          const agentKey = match ? match[1] : 'agent'
          if (!lastLineByAgent.has(agentKey)) agentOrder.push(agentKey)
          lastLineByAgent.set(agentKey, logStr)
        })
        const initialEvents = agentOrder.map((agentKey) => {
          const logStr = lastLineByAgent.get(agentKey)
          return {
            type: 'agent_complete',
            agent: agentKey,
            stage: agentKey,
            preview: logStr,
            timestamp: new Date().toISOString()
          }
        })
        setEvents(initialEvents)
      }
      
      // Sync the WS status with the loaded project status if appropriate.
      // position.phase is checked FIRST because it is the only signal that can
      // tell a genuinely-running project from one the server died under — the
      // row says `running` in both cases. Without this the zombie renders as a
      // spinner that never resolves.
      // NB: interruption is NOT pushed into `status` — that variable is owned by
      // the WebSocket connection lifecycle, which fires 'connected' after this
      // and would clobber it. It is derived from position.phase at render time.
      if (data.status === 'completed') {
        setStatus('done')
      } else if (data.status === 'awaiting_approval') {
        setStatus('awaiting_approval')
      } else if (data.status === 'cancelled') {
        setStatus('cancelled')
      } else if (data.status === 'error_paused') {
        setStatus('error_paused')
      } else if (data.status === 'rate_limited') {
        setStatus('rate_limited')
      } else if (data.status === 'running') {
        setStatus((prev) => (prev === 'awaiting_approval' || prev === 'done' ? 'connecting' : prev))
      }
    } catch (err) {
      console.error(err)
      setMetadataError('Failed to fetch project details. Is the backend running?')
    } finally {
      setMetadataLoading(false)
    }
  }

  useEffect(() => {
    if (projectId) {
      fetchMetadata()
    }
  }, [projectId])

  // Keep projectMetadata (and its next_gate) in sync as the pipeline advances on its own —
  // otherwise the gate card can get stuck showing a stage that already finished.
  useEffect(() => {
    const lastEvent = events[events.length - 1]
    if (!lastEvent) return
    if (['gate_reached', 'pipeline_complete', 'project_cancelled', 'error', 'agent_error', 'rate_limited', 'agent_skipped'].includes(lastEvent.type)) {
      // Clear the regenerating flag only after fresh metadata lands, so the
      // gate card never unmounts (and loses its diff/overlay state) in between.
      fetchMetadata().then(() => {
        setRegeneratingGate(null)
        setCycleInfo(null)
      })
    }
  }, [events])

  // Scroll to bottom when events update
  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [events])

  // Derive the active gate from persisted state first, then fall back to stream events
  const activeGateEvent = projectMetadata?.next_gate
    ? { type: 'gate_reached', gate: projectMetadata.next_gate }
    : events
      .slice()
      .reverse()
      .find(e => e.type === 'gate_reached')

  // Derive current stage from the latest agent_complete event
  const latestCompleteEvent = events
    .slice()
    .reverse()
    .find(e => e.type === 'agent_complete')
  
  const currentStage = latestCompleteEvent ? latestCompleteEvent.stage : (projectMetadata ? projectMetadata.current_stage : '')

  // decision -> the stage a feedback re-run targets, per gate (mirrors GATE_ROUTES)
  const REGEN_TARGETS = {
    human_gate_1: { edit: 'requirements', back: 'research' },
    human_gate_2: { edit: 'architecture', back: 'requirements' },
    human_gate_3: { edit: 'planning', back: 'architecture' },
  }

  const handleResume = async (decision, feedback) => {
    const currentGate = projectMetadata?.next_gate || ''
    const loopsBackToGate = Boolean(REGEN_TARGETS[currentGate]?.[decision])
    if (loopsBackToGate) {
      // Feedback re-run loops back to this same gate — keep its card mounted
      // (with the regenerating overlay) rather than swapping to the live feed.
      setRegeneratingGate(currentGate)
      setCycleInfo({ target: REGEN_TARGETS[currentGate][decision], startedAt: Date.now() })
    } else {
      setStatus('connecting')
    }
    await resumePipeline(decision, feedback)
    // Refetch metadata after a short delay to sync current stage and status
    setTimeout(() => {
      fetchMetadata()
    }, 1000)
  }

  // Mirrors SKIPPABLE_AGENTS in pipeline.py — used when rebuilding the error
  // card from persisted metadata after a reload (no live event to read from).
  const SKIPPABLE_AGENTS = ['research', 'qa', 'devops']

  // Recovery card data: prefer the live event (has retry_in/skippable), fall
  // back to persisted failed_agent/failure_context so a reload or backend
  // restart still shows the card.
  const isErrorState = displayStatusIsError(status)
  const lastErrorEvent = events
    .slice()
    .reverse()
    .find((e) => e.type === 'agent_error' || e.type === 'rate_limited')
  const errorInfo = !isErrorState
    ? null
    : lastErrorEvent
      ? {
          agent: lastErrorEvent.agent,
          error_type: lastErrorEvent.error_type || (lastErrorEvent.type === 'rate_limited' ? 'rate_limit' : 'unknown'),
          message: lastErrorEvent.message,
          skippable: lastErrorEvent.skippable ?? SKIPPABLE_AGENTS.includes(lastErrorEvent.agent),
          rate_limited: lastErrorEvent.type === 'rate_limited',
          retry_in: lastErrorEvent.retry_in,
          cycle: lastErrorEvent.cycle,
          max_cycles: lastErrorEvent.max_cycles,
          receivedAt: new Date(lastErrorEvent.timestamp).getTime() || Date.now(),
        }
      : projectMetadata?.failed_agent
        ? {
            agent: projectMetadata.failed_agent,
            error_type: projectMetadata.failure_context?.error_type || 'unknown',
            message: projectMetadata.failure_context?.message || '',
            skippable: SKIPPABLE_AGENTS.includes(projectMetadata.failed_agent),
            rate_limited: status === 'rate_limited',
            retry_in: 60,
            receivedAt: Date.now(),
          }
        : null

  function displayStatusIsError(s) {
    return s === 'error_paused' || s === 'rate_limited'
  }

  // Zombie recovery: the checkpoint holds a resumable `next`, so re-entering the
  // graph is all that is needed — the resume endpoint restarts streaming from
  // wherever the run died.
  const handleResumeInterrupted = async () => {
    setResuming(true)
    try {
      const res = await fetch(`/api/projects/${projectId}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision: 'approve', feedback: '' }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body?.detail || `Resume failed (HTTP ${res.status})`)
      }
      setStatus('connecting')
      setTimeout(fetchMetadata, 1000)
    } catch (err) {
      console.error(err)
      setResuming(false)
    }
  }

  const handleRecover = async (action) => {
    const res = await fetch(`/api/projects/${projectId}/recover`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body?.detail || `Recovery failed (HTTP ${res.status})`)
    }
    if (action === 'cancel') {
      setStatus('cancelled')
    } else {
      setStatus('connecting')
    }
    setTimeout(fetchMetadata, 800)
  }



  // Skeleton rather than a spinner: it stands in for the real layout, so the
  // page does not jump when the data lands, and it shows WHAT is coming.
  if (metadataLoading && events.length === 0) {
    return (
      <div className="flex h-full flex-col bg-surface">
        <div className="flex shrink-0 items-center justify-between border-b border-line bg-raised px-4 py-3 sm:px-6">
          <div>
            <Skeleton className="h-5 w-48" />
            <Skeleton className="mt-2 h-3 w-72" />
          </div>
          <Skeleton className="h-8 w-56" />
        </div>
        <div className="flex gap-3 border-b border-line px-4 py-3 sm:px-6">
          {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-4 w-20" />)}
        </div>
        <div className="flex-1 space-y-3 overflow-hidden p-4 sm:p-5">
          {Array.from({ length: 4 }, (_, i) => (
            <Card key={i}>
              <Skeleton className="h-3.5 w-32" />
              <SkeletonText lines={2} className="mt-3" />
            </Card>
          ))}
        </div>
      </div>
    )
  }

  if (metadataError && events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] p-6 bg-surface text-center space-y-4">
        <div className="text-err text-4xl">⚠️</div>
        <h2 className="text-lg font-bold text-ink">Failed to Load Project</h2>
        <p className="text-sm text-ink-2">{metadataError}</p>
        <Link to="/" className="text-sm text-run hover:underline">
          &larr; Back to Dashboard
        </Link>
      </div>
    )
  }

  const name = projectMetadata ? projectMetadata.project_name : 'Project Pipeline'
  // position.phase is the authority on whether this project is actually being
  // driven; the WebSocket `status` only describes THIS browser's connection, and
  // a connected socket to a dead run still means the run is dead.
  const isInterrupted = projectMetadata?.position?.phase === 'interrupted' && !resuming
  const displayStatus = isInterrupted
    ? 'interrupted'
    : status === 'connecting' || status === 'reconnecting' ? 'running' : status === 'done' ? 'completed' : status

  // While a feedback re-run is in flight, next_gate goes null in refetches —
  // fall back to the gate we were on so its card stays mounted.
  const gateName = regeneratingGate || projectMetadata?.next_gate || activeGateEvent?.gate || ''
  const isFullWidthGate =
    (displayStatus === 'awaiting_approval' || regeneratingGate) &&
    ['human_gate_1', 'human_gate_2', 'human_gate_3', 'human_gate_4'].includes(gateName)

  return (
    <div className="flex h-full flex-col bg-surface">
      {/* Project Header Bar */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line bg-raised px-4 py-3 sm:px-6">
        <div>
          <h1 className="truncate text-lg font-semibold tracking-tight text-ink">{name}</h1>
          <p className="mt-0.5 max-w-2xl truncate text-[12px] text-ink-3">
            Brief: {projectMetadata?.brief}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={statusMeta(displayStatus).tone}>
            <Dot tone={statusMeta(displayStatus).tone} />
            {statusMeta(displayStatus).label}
          </Badge>
          {fileCount > 0 && (
            <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-ok/10 text-ok border border-ok/35">
              {fileCount} files generated
            </span>
          )}
          <button
            type="button"
            onClick={() => setRestarting(true)}
            disabled={displayStatus === 'running'}
            title={displayStatus === 'running' ? 'Cannot restart while the pipeline is running' : 'Re-run from a chosen stage'}
            className="rounded border border-line bg-overlay px-3 py-1.5 text-xs font-semibold text-ink-2 transition-colors hover:text-ink disabled:opacity-40"
          >
            Restart
          </button>
          <button
            type="button"
            onClick={() => setDeleting(true)}
            className="rounded border border-line bg-overlay px-3 py-1.5 text-xs font-semibold text-err transition-colors hover:bg-err/10"
          >
            Delete
          </button>
          <Link
            to="/"
            className="text-xs font-semibold text-ink-2 hover:text-ink bg-overlay border border-line px-3 py-1.5 rounded transition-colors"
          >
            Dashboard
          </Link>
        </div>
      </div>

      {projectMetadata && (
        <StageTimeline
          projectState={projectMetadata}
          status={displayStatus}
          events={events}
          regenerating={Boolean(regeneratingGate)}
          cycleInfo={cycleInfo}
        />
      )}

      {/* Compact token/latency rollup. Rendered only once a run has reached a
          settled state — on a project still in research the "no metrics yet"
          empty state would read as an error rather than as "too early".
          Suppressed at gate 4, which renders the full panel itself; showing both
          put two identical "Run Metrics" cards on the same screen.
          Fetches once on mount; no polling, since the numbers only matter after
          a stage completes. */}
      {(displayStatus === 'completed' || displayStatus === 'awaiting_approval')
        && !gateName.includes('gate_4') && (
        <div className="border-b border-line px-4 py-3 sm:px-6">
          <div className="mx-auto max-w-6xl">
            <MetricsPanel projectId={projectId} compact />
          </div>
        </div>
      )}

      {/* Zombie card: the server died mid-node. The checkpoint still holds a
          resumable position, so this offers Resume rather than a dead spinner. */}
      {displayStatus === 'interrupted' && (
        <div className="border-b border-line px-4 py-3 sm:px-6">
          <div className="mx-auto max-w-6xl rounded-lg border border-alt/35 bg-alt/10 p-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-bold text-alt">This run was interrupted</p>
                <p className="mt-0.5 text-xs text-alt">
                  The backend stopped while{' '}
                  <span className="font-semibold">{stageLabel(projectMetadata?.position?.next_node)}</span>{' '}
                  was running. Nothing was lost — the last checkpoint is intact and the
                  pipeline can carry on from there.
                </p>
              </div>
              <button
                type="button"
                onClick={handleResumeInterrupted}
                disabled={resuming}
                className="flex-shrink-0 rounded bg-overlay px-4 py-2 text-sm font-semibold text-ink hover:bg-line disabled:opacity-50"
              >
                {resuming ? 'Resuming…' : 'Resume'}
              </button>
            </div>
          </div>
        </div>
      )}

      {errorInfo && (
        <div className="border-b border-line px-4 py-3 sm:px-6">
          <div className="mx-auto max-w-6xl">
            <ErrorCard info={errorInfo} onRecover={handleRecover} />
          </div>
        </div>
      )}

      {/* Permanent record — stats, files and exports from persisted state.
          Suppressed at gate 4, which renders its own richer file browser. */}
      {projectMetadata && !isFullWidthGate
        && ['completed', 'cancelled', 'interrupted', 'error_paused'].includes(displayStatus) && (
        <div className="border-b border-line px-4 py-3 sm:px-6">
          <div className="mx-auto max-w-6xl">
            <ProjectRecord projectId={projectId} projectState={projectMetadata} />
          </div>
        </div>
      )}

      {isFullWidthGate ? (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto max-w-6xl rounded-lg border border-accent/35 bg-raised p-6 ">
            {gateName === 'human_gate_1' ? (
              <Gate1Approval
                projectId={projectId}
                projectState={projectMetadata}
                status={displayStatus}
                onResume={handleResume}
                onRefresh={fetchMetadata}
                lastAgentComplete={latestCompleteEvent?.agent || ''}
              />
            ) : gateName === 'human_gate_2' ? (
              <Gate2Approval
                projectId={projectId}
                projectState={projectMetadata}
                status={displayStatus}
                onResume={handleResume}
                onRefresh={fetchMetadata}
                lastAgentComplete={latestCompleteEvent?.agent || ''}
              />
            ) : gateName === 'human_gate_3' ? (
              <Gate3Approval
                projectId={projectId}
                projectState={projectMetadata}
                status={displayStatus}
                onResume={handleResume}
                lastAgentComplete={latestCompleteEvent?.agent || ''}
              />
            ) : (
              <Gate4Approval
                projectId={projectId}
                projectState={projectMetadata}
                status={displayStatus}
                onResume={handleResume}
              />
            )}
          </div>
        </div>
      ) : (
      /* Main Two-Column Layout */
      <div className="flex-1 flex overflow-hidden">
        {/* Left Column: Live Event Timeline */}
        <div className="flex h-full w-full flex-col border-r border-line bg-surface lg:w-[65%]">
          <div className="flex shrink-0 items-center justify-between border-b border-line bg-raised px-4 py-2.5 sm:px-6">
            <h3 className="eyebrow">Live agent stream</h3>
            <div className="flex items-center gap-3">
              <span className="text-xs text-ink-3 bg-overlay px-2 py-0.5 rounded font-mono">
                Status: {status}
              </span>
              {/* File counter badge */}
              <span className="text-xs text-ink-2 bg-run/10 border border-run/35 px-2 py-0.5 rounded font-mono">
                {events.filter(e => e.type === 'file_written').length} files generated
              </span>
            </div>
          </div>

          <div className="flex-1 space-y-3 overflow-y-auto p-4 sm:p-5">
            {phaseProgress.length > 0 && (
              <div className="space-y-2 mb-2">
                {phaseProgress.map((p) => (
                  <PhaseProgress key={p.phase} phase={p} />
                ))}
                <QaStreamIndicator
                  qa={deriveQaStream(events)}
                  generating={phaseProgress.some((p) => !p.complete)}
                />
              </div>
            )}
            {events.length === 0 && status === 'connecting' ? (
              <div className="flex flex-col items-center justify-center h-full text-center space-y-4 animate-pulse">
                <svg className="animate-spin h-8 w-8 text-run" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                <div className="text-sm font-medium text-ink-2">Connecting to pipeline...</div>
              </div>
            ) : events.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center text-ink-3">
                <p className="text-sm italic">No events streamed yet. Starting up...</p>
              </div>
            ) : (
              <div className="relative space-y-2.5 before:absolute before:top-2 before:bottom-2 before:left-[5.5px] before:w-px before:bg-line">
                {events.map((event, index) => (
                  <FeedEvent key={index} event={event} />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Gate Review or Indicator */}
        <div className="hidden w-[35%] overflow-y-auto bg-surface p-5 lg:block">
          <ApprovalGate
            status={displayStatus}
            gateEvent={activeGateEvent}
            currentStage={currentStage}
            eventsCount={events.length}
            onResume={handleResume}
            projectId={projectId}
            initialProjectState={projectMetadata}
          />
        </div>
      </div>
      )}

      {restarting && (
        <RestartDialog
          projectId={projectId}
          projectName={name}
          onClose={() => setRestarting(false)}
          onDone={() => {
            setStatus('connecting')
            setEvents([])
            setTimeout(fetchMetadata, 1000)
          }}
        />
      )}
      {deleting && (
        <DeleteDialog
          projectId={projectId}
          projectName={name}
          status={displayStatus}
          onClose={() => setDeleting(false)}
          onDone={() => { window.location.href = '/' }}
        />
      )}
    </div>
  )
}
