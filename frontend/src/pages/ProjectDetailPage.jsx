import React, { useEffect, useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useProjectStream } from '../hooks/useProjectStream'
import ApprovalGate from '../components/ApprovalGate'
import StageTimeline from '../components/StageTimeline'
import Gate1Approval from '../components/gates/Gate1Approval'
import Gate2Approval from '../components/gates/Gate2Approval'
import Gate3Approval from '../components/gates/Gate3Approval'
import Gate4Approval from '../components/gates/Gate4Approval'
import ErrorCard from '../components/ErrorCard'
import PhaseProgress, { derivePhaseProgress } from '../components/PhaseProgress'

const MARKDOWN_AGENTS = new Set(['research', 'requirements', 'architecture', 'qa', 'planning'])

function EventOutput({ event, defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const fullContent = event.content || event.preview || event.output_preview || event.message || ''
  const shortPreview = (event.preview || event.output_preview || fullContent).slice(0, 160)
  const isMarkdown = MARKDOWN_AGENTS.has(event.agent)

  if (!fullContent) return null

  return (
    <div className="my-2 rounded border border-gray-100 bg-gray-50">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 border-b border-gray-200 px-3 py-2 text-left"
      >
        <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
          Agent Output
        </span>
        <span className="text-[11px] font-semibold text-blue-600 hover:text-blue-700">
          {expanded ? 'Collapse' : 'Click to view full output'}
        </span>
      </button>

      {expanded ? (
        <div className="max-h-96 overflow-auto p-3">
          {isMarkdown ? (
            <div className="prose prose-sm max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{fullContent}</ReactMarkdown>
            </div>
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-sm leading-relaxed text-gray-600">
              {fullContent}
            </pre>
          )}
        </div>
      ) : (
        <div className="p-3">
          <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-gray-500 line-clamp-2">
            {shortPreview}
          </p>
        </div>
      )}
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
      const res = await fetch(`http://localhost:8000/api/projects/${projectId}`)
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
      
      // Sync the WS status with the loaded project status if appropriate
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

  const handleRecover = async (action) => {
    const res = await fetch(`http://localhost:8000/api/projects/${projectId}/recover`, {
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

  // Helper for border and badge colors
  const getEventStyle = (type) => {
    switch (type) {
      case 'agent_complete':
      case 'pipeline_complete':
        return {
          border: 'border-l-4 border-green-500',
          badge: 'bg-green-100 text-green-800'
        }
      case 'gate_reached':
        return {
          border: 'border-l-4 border-orange-500',
          badge: 'bg-orange-100 text-orange-800'
        }
      case 'file_written':
        return {
          border: 'border-l-4 border-blue-500',
          badge: 'bg-blue-100 text-blue-800'
        }
      // Day 22: automated checks get their own colour so the pass reads as a
      // distinct step between coding and QA, not as another agent card.
      case 'validation_complete':
        return {
          border: 'border-l-4 border-purple-500',
          badge: 'bg-purple-100 text-purple-800'
        }
      case 'error':
      case 'agent_error':
        return {
          border: 'border-l-4 border-red-500',
          badge: 'bg-red-100 text-red-800'
        }
      case 'rate_limited':
        return {
          border: 'border-l-4 border-amber-500',
          badge: 'bg-amber-100 text-amber-800'
        }
      case 'agent_skipped':
        return {
          border: 'border-l-4 border-gray-400',
          badge: 'bg-gray-200 text-gray-700'
        }
      default:
        return {
          border: 'border-l-4 border-gray-300',
          badge: 'bg-gray-100 text-gray-800'
        }
    }
  }

  const getStatusBadgeStyle = (projectStatus) => {
    switch (projectStatus) {
      case 'completed':
      case 'done':
        return 'bg-green-100 text-green-800 border-green-200'
      case 'awaiting_approval':
        return 'bg-orange-100 text-orange-800 border-orange-200'
      case 'running':
        return 'bg-blue-100 text-blue-800 border-blue-200'
      case 'error':
      case 'error_paused':
        return 'bg-red-100 text-red-800 border-red-200'
      case 'rate_limited':
        return 'bg-amber-100 text-amber-800 border-amber-200'
      case 'cancelled':
        return 'bg-gray-200 text-gray-700 border-gray-300'
      default:
        return 'bg-gray-100 text-gray-800 border-gray-200'
    }
  }

  const formatTime = (isoString) => {
    try {
      const d = new Date(isoString)
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    } catch (e) {
      return isoString
    }
  }

  if (metadataLoading && events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] p-6 bg-[#f9fafb]">
        <div className="flex items-center space-x-3 text-gray-500 animate-pulse">
          <svg className="animate-spin h-6 w-6 text-blue-600" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          <span className="font-medium">Loading project info...</span>
        </div>
      </div>
    )
  }

  if (metadataError && events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] p-6 bg-[#f9fafb] text-center space-y-4">
        <div className="text-red-500 text-4xl">⚠️</div>
        <h2 className="text-lg font-bold text-gray-900">Failed to Load Project</h2>
        <p className="text-sm text-gray-600">{metadataError}</p>
        <Link to="/" className="text-sm text-blue-600 hover:underline">
          &larr; Back to Dashboard
        </Link>
      </div>
    )
  }

  const name = projectMetadata ? projectMetadata.project_name : 'Project Pipeline'
  const displayStatus = status === 'connecting' || status === 'reconnecting' ? 'running' : status === 'done' ? 'completed' : status

  // While a feedback re-run is in flight, next_gate goes null in refetches —
  // fall back to the gate we were on so its card stays mounted.
  const gateName = regeneratingGate || projectMetadata?.next_gate || activeGateEvent?.gate || ''
  const isFullWidthGate =
    (displayStatus === 'awaiting_approval' || regeneratingGate) &&
    ['human_gate_1', 'human_gate_2', 'human_gate_3', 'human_gate_4'].includes(gateName)

  return (
    <div className="flex flex-col h-full bg-[#f9fafb]">
      {/* Project Header Bar */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between shadow-xs flex-shrink-0">
        <div>
          <h1 className="text-xl font-bold text-gray-900 truncate">{name}</h1>
          <p className="text-xs text-gray-500 truncate max-w-2xl mt-0.5">
            Brief: {projectMetadata?.brief}
          </p>
        </div>
        <div className="flex items-center space-x-3">
          <span className={`px-2.5 py-1 text-xs font-semibold rounded-full border ${getStatusBadgeStyle(displayStatus)}`}>
            {displayStatus.replace('_', ' ').toUpperCase()}
          </span>
          {fileCount > 0 && (
            <span className="px-2.5 py-1 text-xs font-semibold rounded-full bg-green-100 text-green-800 border border-green-200">
              {fileCount} files generated
            </span>
          )}
          <Link
            to="/"
            className="text-xs font-semibold text-gray-600 hover:text-gray-900 bg-gray-50 border border-gray-200 px-3 py-1.5 rounded transition-colors"
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

      {errorInfo && (
        <div className="border-b border-gray-200 bg-[#f9fafb] px-6 py-3">
          <div className="mx-auto max-w-6xl">
            <ErrorCard info={errorInfo} onRecover={handleRecover} />
          </div>
        </div>
      )}

      {isFullWidthGate ? (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto max-w-6xl rounded-lg border border-orange-200 bg-white p-6 shadow-sm">
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
        <div className="w-[65%] flex flex-col h-full border-r border-gray-200 bg-white">
          <div className="bg-gray-50 px-6 py-3 border-b border-gray-200 flex justify-between items-center">
            <h3 className="text-sm font-semibold text-gray-700">Live Agent Stream</h3>
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-500 bg-gray-200 px-2 py-0.5 rounded font-mono">
                Status: {status}
              </span>
              {/* File counter badge */}
              <span className="text-xs text-gray-600 bg-blue-50 border border-blue-200 px-2 py-0.5 rounded font-mono">
                {events.filter(e => e.type === 'file_written').length} files generated
              </span>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {phaseProgress.length > 0 && (
              <div className="space-y-2 mb-2">
                {phaseProgress.map((p) => (
                  <PhaseProgress key={p.phase} phase={p} />
                ))}
              </div>
            )}
            {events.length === 0 && status === 'connecting' ? (
              <div className="flex flex-col items-center justify-center h-full text-center space-y-4 animate-pulse">
                <svg className="animate-spin h-8 w-8 text-blue-600" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                <div className="text-sm font-medium text-gray-600">Connecting to pipeline...</div>
              </div>
            ) : events.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center text-gray-500">
                <p className="text-sm italic">No events streamed yet. Starting up...</p>
              </div>
            ) : (
              <div className="space-y-4 relative before:absolute before:top-2 before:bottom-2 before:left-[13px] before:w-[2px] before:bg-gray-100">
                {events.map((event, index) => {
                  const styles = getEventStyle(event.type)
                  
                  // Day 22 automated-checks card: counts at a glance, amber when
                  // the run finished below the quality threshold.
                  if (event.type === 'validation_complete') {
                    const importWarnings = event.import_warnings || 0
                    const below = event.below_threshold
                    return (
                      <div key={index} className="relative flex items-start space-x-4 pl-8">
                        <span className={`absolute left-0.5 top-1.5 h-6.5 w-6.5 rounded-full border-4 border-white flex items-center justify-center shadow-xs ${below ? 'bg-amber-500' : 'bg-purple-500'}`} />
                        <div className={`flex-1 bg-white p-3 rounded-lg border shadow-xs border-l-4 ${below ? 'border-amber-500 border-amber-100' : 'border-purple-500 border-purple-100'}`}>
                          <div className="flex items-center gap-2">
                            <span className="text-sm">{below ? '⚠' : '🔍'}</span>
                            <span className="text-xs font-bold uppercase tracking-wide text-gray-800">
                              Automated Checks
                            </span>
                            <span className="ml-auto font-mono text-[11px] text-gray-400">
                              {event.files_checked} files
                            </span>
                          </div>
                          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-600">
                            <span>{event.syntax_errors} unresolved syntax</span>
                            <span>{event.auto_repaired} auto-repaired</span>
                            <span>{importWarnings} import warnings</span>
                            {event.artifact_errors > 0 && <span>{event.artifact_errors} invalid JSON/YAML</span>}
                            <span className="text-gray-400">
                              repairs {event.repair_calls_spent}/{event.repair_ceiling}
                            </span>
                          </div>
                          <div className="mt-1 text-[10px] font-medium text-gray-400">
                            {formatTime(event.timestamp)}
                          </div>
                        </div>
                      </div>
                    )
                  }

                  // Special rendering for per-file events (written / failed / blocked).
                  if (event.type === 'file_written' || event.type === 'file_failed' || event.type === 'file_blocked') {
                    const kind = event.type === 'file_written'
                      ? { dot: 'bg-green-500', border: 'border-green-500', card: 'border-green-100', icon: '✓', iconColor: 'text-green-500' }
                      : event.type === 'file_failed'
                        ? { dot: 'bg-red-500', border: 'border-red-500', card: 'border-red-100', icon: '✕', iconColor: 'text-red-500' }
                        : { dot: 'bg-gray-400', border: 'border-gray-400', card: 'border-gray-200', icon: '⊘', iconColor: 'text-gray-500' }
                    return (
                      <div key={index} className="relative flex items-start space-x-4 pl-8">
                        <span className={`absolute left-0.5 top-1.5 h-6.5 w-6.5 rounded-full border-4 border-white flex items-center justify-center shadow-xs ${kind.dot}`} />
                        <div className={`flex-1 bg-white p-3 rounded-lg border ${kind.card} shadow-xs border-l-4 ${kind.border}`}>
                          <div className="flex items-center gap-2">
                            <span className={`${kind.iconColor} text-sm`}>{event.type === 'file_blocked' ? '⊘' : kind.icon}</span>
                            <span className="font-mono text-xs text-gray-700">{event.filepath}</span>
                            {event.total != null ? (
                              <span className="text-gray-400 text-xs ml-auto font-mono">
                                {event.done}/{event.total}
                              </span>
                            ) : event.progress ? (
                              <span className="text-gray-400 text-xs ml-auto font-mono">{event.progress}</span>
                            ) : null}
                          </div>
                          {(event.error || event.reason) && (
                            <div className="text-[11px] text-gray-500 mt-1 font-mono truncate">
                              {event.type === 'file_blocked' ? event.reason : event.error}
                            </div>
                          )}
                          <div className="text-[10px] text-gray-400 mt-1 font-medium">
                            {formatTime(event.timestamp)}
                          </div>
                        </div>
                      </div>
                    )
                  }
                  
                  return (
                    <div 
                      key={index} 
                      className={`relative flex items-start space-x-4 pl-8 group`}
                    >
                      {/* Timeline Dot */}
                      <span className={`absolute left-0.5 top-1.5 h-6.5 w-6.5 rounded-full border-4 border-white flex items-center justify-center shadow-xs ${
                        event.type === 'agent_complete' ? 'bg-green-500' :
                        event.type === 'gate_reached' ? 'bg-orange-500' :
                        event.type === 'error' ? 'bg-red-500' : 'bg-gray-400'
                      }`} />

                      {/* Event Card */}
                      <div className={`flex-1 bg-white p-4 rounded-lg border border-gray-100 shadow-xs hover:shadow-sm transition-all duration-200 ${styles.border}`}>
                        <div className="flex justify-between items-start mb-1">
                          <h4 className="text-sm font-bold text-gray-800 capitalize">
                            {event.agent ? event.agent.replace('_', ' ') : 'System Agent'}
                          </h4>
                          <span className={`px-2 py-0.5 text-[10px] font-semibold rounded uppercase tracking-wide ${styles.badge}`}>
                            {event.type.replace('_', ' ')}
                          </span>
                        </div>

                        {(event.content || event.preview || event.output_preview || event.message) && (
                          <EventOutput event={event} />
                        )}

                        <div className="text-[10px] text-gray-400 mt-2 font-medium">
                          {formatTime(event.timestamp)}
                        </div>
                      </div>
                    </div>
                  )
                })}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Gate Review or Indicator */}
        <div className="w-[35%] bg-gray-50 p-6 overflow-y-auto">
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
    </div>
  )
}
