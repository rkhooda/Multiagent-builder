import React, { useEffect, useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useProjectStream } from '../hooks/useProjectStream'
import ApprovalGate from '../components/ApprovalGate'

export default function ProjectDetailPage() {
  const { projectId } = useParams()
  
  const [projectMetadata, setProjectMetadata] = useState(null)
  const [metadataLoading, setMetadataLoading] = useState(true)
  const [metadataError, setMetadataError] = useState('')
  
  const { events, setEvents, status, setStatus, resumePipeline } = useProjectStream(projectId)
  const bottomRef = useRef(null)

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
      // so the page doesn't look empty when reloaded
      if (data.log && data.log.length > 0 && events.length === 0) {
        const initialEvents = data.log.map((logStr, index) => {
          // Parse stage/agent name if possible from log string
          // Typical log: "research ran" or similar
          const parts = logStr.split(' ')
          const stageName = parts[0] || 'agent'
          return {
            type: 'agent_complete',
            agent: stageName,
            stage: stageName,
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

  const handleResume = async (decision, feedback) => {
    // If approving or editing, first set status back to connecting/running
    setStatus('connecting')
    await resumePipeline(decision, feedback)
    // Refetch metadata after a short delay to sync current stage and status
    setTimeout(() => {
      fetchMetadata()
    }, 1000)
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
      case 'error':
        return {
          border: 'border-l-4 border-red-500',
          badge: 'bg-red-100 text-red-800'
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
        return 'bg-red-100 text-red-800 border-red-200'
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

  const name = projectMetadata ? projectMetadata.name : 'Project Pipeline'
  const displayStatus = status === 'connecting' || status === 'reconnecting' ? 'running' : status === 'done' ? 'completed' : status

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
          <Link
            to="/"
            className="text-xs font-semibold text-gray-600 hover:text-gray-900 bg-gray-50 border border-gray-200 px-3 py-1.5 rounded transition-colors"
          >
            Dashboard
          </Link>
        </div>
      </div>

      {/* Main Two-Column Layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Column: Live Event Timeline */}
        <div className="w-[65%] flex flex-col h-full border-r border-gray-200 bg-white">
          <div className="bg-gray-50 px-6 py-3 border-b border-gray-200 flex justify-between items-center">
            <h3 className="text-sm font-semibold text-gray-700">Live Agent Stream</h3>
            <span className="text-xs text-gray-500 bg-gray-200 px-2 py-0.5 rounded font-mono">
              Status: {status}
            </span>
          </div>

          <div className="flex-1 overflow-y-auto p-6 space-y-4">
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

                        {event.preview && (
                          <p className="text-sm text-gray-600 leading-relaxed font-mono bg-gray-50 p-2.5 rounded border border-gray-100 my-2 overflow-x-auto whitespace-pre-wrap">
                            {event.preview}
                          </p>
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
    </div>
  )
}
