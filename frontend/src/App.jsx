import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const AGENT_TYPES = [
  { value: 'research', label: 'Research (Gemini 2.5 Flash / Groq Llama 3.3)' },
  { value: 'requirements', label: 'Requirements (Gemini 2.5 Flash / DeepSeek V3)' },
  { value: 'architecture', label: 'Architecture (DeepSeek V3 / Gemini 2.5 Flash)' },
  { value: 'planning', label: 'Planning (Gemini 2.5 Flash / Groq Llama 3.3)' },
  { value: 'frontend_code', label: 'Frontend Code (Qwen Coder / DeepSeek V3)' },
  { value: 'backend_code', label: 'Backend Code (DeepSeek V3 / Qwen Coder)' },
  { value: 'database', label: 'Database (Groq Llama 3.3 / DeepSeek V3)' },
  { value: 'qa', label: 'QA (DeepSeek R1 / Gemini 2.5 Flash)' },
  { value: 'devops', label: 'Devops (Groq Llama 3.3 / Gemini 2.5 Flash)' }
]

const PIPELINE_STAGES = [
  'research',
  'requirements',
  'architecture',
  'planning',
  'frontend_code',
  'backend_code',
  'database',
  'qa',
  'devops'
]

function App() {
  // Navigation
  const [view, setView] = useState('home') // 'home' | 'detail' | 'playground'

  // Health states
  const [health, setHealth] = useState(null)
  const [healthLoading, setHealthLoading] = useState(true)

  // Projects list state
  const [projects, setProjects] = useState([])
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [projectsError, setProjectsError] = useState(null)

  // Create Project state
  const [newProjectName, setNewProjectName] = useState('')
  const [newProjectBrief, setNewProjectBrief] = useState('')
  const [createLoading, setCreateLoading] = useState(false)
  const [createError, setCreateError] = useState(null)

  // Project Detail state
  const [selectedProjectId, setSelectedProjectId] = useState(null)
  const [projectDetail, setProjectDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(null)
  
  // WS Event Feed state
  const [wsEvents, setWsEvents] = useState([])
  const [wsStatus, setWsStatus] = useState('disconnected')

  // Resume Form state
  const [feedbackText, setFeedbackText] = useState('')
  const [submittingResume, setSubmittingResume] = useState(false)
  const [resumeError, setResumeError] = useState(null)

  // Detail Doc Tab state
  const [activeDocTab, setActiveDocTab] = useState('research')
  const [selectedCodeFile, setSelectedCodeFile] = useState(null)

  // LLM Test states (Playground)
  const [agentType, setAgentType] = useState('research')
  const [prompt, setPrompt] = useState('Say hello in exactly 10 words')
  const [testResult, setTestResult] = useState('')
  const [testLoading, setTestLoading] = useState(false)
  const [testError, setTestError] = useState(null)

  // Keep a ref to projectDetail for the WebSocket reconnection logic
  const projectDetailRef = useRef(projectDetail)
  useEffect(() => {
    projectDetailRef.current = projectDetail
  }, [projectDetail])

  // Fetch health on load
  useEffect(() => {
    axios.get('http://localhost:8000/health')
      .then(response => {
        setHealth(response.data)
        setHealthLoading(false)
      })
      .catch(err => {
        console.error("Health check failed:", err)
        setHealthLoading(false)
      })
  }, [])

  // Fetch projects list
  const fetchProjects = () => {
    setProjectsLoading(true)
    setProjectsError(null)
    axios.get('http://localhost:8000/api/projects')
      .then(response => {
        setProjects(response.data)
        setProjectsLoading(false)
      })
      .catch(err => {
        console.error("Error fetching projects:", err)
        setProjectsError(err.message || 'Failed to connect to backend')
        setProjectsLoading(false)
      })
  }

  // Load projects list when home mounts
  useEffect(() => {
    if (view === 'home') {
      fetchProjects()
    }
  }, [view])

  // Fetch individual project detail snapshot
  const fetchProjectDetail = (id, quiet = false) => {
    if (!quiet) setDetailLoading(true)
    setDetailError(null)
    axios.get(`http://localhost:8000/api/projects/${id}`)
      .then(response => {
        setProjectDetail(response.data)
        // If there's a log, sync it into our WS events buffer as historic runs
        if (response.data.log && wsEvents.length === 0) {
          const parsedLogs = response.data.log.map((logEntry, idx) => ({
            type: 'agent_complete',
            agent: logEntry.split(' ')[0] || 'agent',
            stage: logEntry.split(' ')[0] || 'agent',
            preview: logEntry,
            timestamp: `History #${idx + 1}`
          }))
          setWsEvents(parsedLogs)
        }
        
        // Auto select first file if files exist
        if (response.data.file_list && response.data.file_list.length > 0 && !selectedCodeFile) {
          setSelectedCodeFile(response.data.file_list[0])
        }

        if (!quiet) setDetailLoading(false)
      })
      .catch(err => {
        console.error("Error fetching project detail:", err)
        if (!quiet) {
          setDetailError(err.response?.data?.detail || err.message || 'Failed to fetch project details')
          setDetailLoading(false)
        }
      })
  }

  // Effect to manage WebSocket connection
  useEffect(() => {
    if (!selectedProjectId || view !== 'detail') return

    let ws
    let reconnectTimeout
    let isMounted = true

    function connect() {
      if (!isMounted) return
      console.log(`Connecting to WebSocket for project ${selectedProjectId}...`)
      setWsStatus('connecting')
      
      ws = new WebSocket(`ws://localhost:8000/ws/projects/${selectedProjectId}`)

      ws.onopen = () => {
        if (!isMounted) return
        setWsStatus('connected')
        setResumeError(null)
      }

      ws.onmessage = (event) => {
        if (!isMounted) return
        try {
          const data = JSON.parse(event.data)
          
          if (data.type === 'heartbeat') {
            return // Skip rendering/buffering heartbeats
          }

          const newEvent = {
            ...data,
            timestamp: new Date().toLocaleTimeString()
          }

          setWsEvents(prev => {
            // Avoid duplicate events
            const exists = prev.some(e => 
              e.type === data.type && 
              e.agent === data.agent && 
              e.gate === data.gate && 
              e.stage === data.stage && 
              e.preview === data.preview
            )
            if (exists) return prev
            return [...prev, newEvent]
          })

          // Proactively update local detail state from events
          if (data.type === 'agent_complete') {
            setProjectDetail(prev => prev ? { ...prev, current_stage: data.stage, status: 'running' } : null)
            // Trigger quiet sync of files or documents
            fetchProjectDetail(selectedProjectId, true)
          } else if (data.type === 'gate_reached') {
            setProjectDetail(prev => prev ? { ...prev, status: 'awaiting_approval', next_gate: data.gate } : null)
            fetchProjectDetail(selectedProjectId, true)
          } else if (data.type === 'pipeline_complete') {
            setProjectDetail(prev => prev ? { ...prev, status: 'completed', current_stage: 'completed', next_gate: null } : null)
            fetchProjectDetail(selectedProjectId, true)
          } else if (data.type === 'error') {
            setProjectDetail(prev => prev ? { ...prev, status: 'error', errors: [...(prev.errors || []), data.message] } : null)
          }
        } catch (err) {
          console.error("Error parsing websocket message:", err)
        }
      }

      ws.onclose = () => {
        if (!isMounted) return
        setWsStatus('disconnected')
        
        // Auto-reconnect if pipeline is still executing
        const currentDetail = projectDetailRef.current
        const isFinished = currentDetail && (currentDetail.status === 'completed' || currentDetail.status === 'error')
        
        if (!isFinished && isMounted) {
          console.log("WebSocket closed. Attempting reconnect in 2 seconds...")
          reconnectTimeout = setTimeout(connect, 2000)
        }
      }

      ws.onerror = (err) => {
        console.error("WebSocket connection error:", err)
      }
    }

    // Trigger connection
    connect()

    return () => {
      isMounted = false
      clearTimeout(reconnectTimeout)
      if (ws) {
        ws.close()
      }
    }
  }, [selectedProjectId, view])

  // Trigger project fetch details on selection
  useEffect(() => {
    if (selectedProjectId && view === 'detail') {
      setWsEvents([])
      setSelectedCodeFile(null)
      fetchProjectDetail(selectedProjectId)
    }
  }, [selectedProjectId, view])

  // Handle Project Creation
  const handleCreateProject = (e) => {
    e.preventDefault()
    if (!newProjectName.trim() || !newProjectBrief.trim()) return

    setCreateLoading(true)
    setCreateError(null)

    axios.post('http://localhost:8000/api/projects', {
      project_name: newProjectName,
      brief: newProjectBrief
    })
      .then(response => {
        setCreateLoading(false)
        setNewProjectName('')
        setNewProjectBrief('')
        // Open the newly created project detail view immediately
        setSelectedProjectId(response.data.project_id)
        setView('detail')
      })
      .catch(err => {
        console.error("Error creating project:", err)
        setCreateError(err.response?.data?.detail || err.message || 'Failed to create project')
        setCreateLoading(false)
      })
  }

  // Handle Resume (Approval Gate Decisions)
  const handleResumeGate = (decision) => {
    setSubmittingResume(true)
    setResumeError(null)

    axios.post(`http://localhost:8000/api/projects/${selectedProjectId}/resume`, {
      decision: decision,
      feedback: feedbackText
    })
      .then(() => {
        setFeedbackText('')
        setSubmittingResume(false)
        // Refresh project detail representation to show 'running'
        setProjectDetail(prev => prev ? { ...prev, status: 'running' } : null)
      })
      .catch(err => {
        console.error("Error resuming project:", err)
        setResumeError(err.response?.data?.detail || err.message || 'Failed to resume pipeline')
        setSubmittingResume(false)
      })
  }

  // Handle LLM Playground Test submit
  const handleTestLLM = (e) => {
    e.preventDefault()
    setTestLoading(true)
    setTestError(null)
    setTestResult('')

    axios.post('http://localhost:8000/api/test-llm', {
      prompt: prompt,
      agent_type: agentType
    })
      .then(response => {
        setTestResult(response.data.response)
        setTestLoading(false)
      })
      .catch(err => {
        console.error("LLM Test Error:", err)
        const errorMsg = err.response?.data?.detail || err.message || 'Failed to call LLM'
        setTestError(errorMsg)
        setTestLoading(false)
      })
  }

  // Helper status color rendering
  const getStatusBadgeClass = (status) => {
    switch (status) {
      case 'running':
        return 'bg-blue-500/10 border border-blue-500/30 text-blue-400 shadow-[0_0_12px_rgba(59,130,246,0.1)] animate-pulse'
      case 'awaiting_approval':
        return 'bg-amber-500/10 border border-amber-500/30 text-amber-400 shadow-[0_0_12px_rgba(245,158,11,0.1)]'
      case 'completed':
        return 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400'
      case 'error':
        return 'bg-rose-500/10 border border-rose-500/30 text-rose-400'
      default:
        return 'bg-gray-500/10 border border-gray-500/30 text-gray-400'
    }
  }

  const getStatusLabel = (status) => {
    switch (status) {
      case 'running': return 'Running Pipeline'
      case 'awaiting_approval': return 'Awaiting Approval'
      case 'completed': return 'Completed'
      case 'error': return 'Execution Error'
      default: return status
    }
  }

  return (
    <div className="min-h-screen bg-[#090a0f] text-[#f3f4f6] flex flex-col font-sans">
      {/* Dynamic Background Glows */}
      <div className="absolute top-[-30%] left-[-20%] w-[800px] h-[800px] rounded-full bg-purple-950/10 blur-[150px] pointer-events-none" />
      <div className="absolute bottom-[-30%] right-[-20%] w-[800px] h-[800px] rounded-full bg-blue-950/10 blur-[150px] pointer-events-none" />

      {/* Global Header */}
      <header className="border-b border-white/5 bg-[#0f1016]/80 backdrop-blur-md sticky top-0 z-50 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center space-x-3 cursor-pointer" onClick={() => setView('home')}>
          <div className="h-9 w-9 bg-gradient-to-tr from-purple-600 to-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-purple-500/20">
            <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path>
            </svg>
          </div>
          <div>
            <span className="text-lg font-extrabold tracking-tight bg-gradient-to-r from-purple-400 via-indigo-300 to-blue-400 bg-clip-text text-transparent">
              Multi-Agent AI Builder
            </span>
            <span className="text-[10px] text-gray-500 font-mono block">v0.1.0 • Orchestrator Console</span>
          </div>
        </div>

        {/* Global Navigation */}
        <nav className="flex items-center space-x-2 bg-white/5 p-1 rounded-xl border border-white/5">
          <button 
            onClick={() => setView('home')}
            className={`px-4 py-1.5 text-xs font-semibold rounded-lg transition-all ${view === 'home' || view === 'detail' ? 'bg-[#1e1f29] text-white shadow-md' : 'text-gray-400 hover:text-gray-200'}`}
          >
            Dashboard
          </button>
          <button 
            onClick={() => setView('playground')}
            className={`px-4 py-1.5 text-xs font-semibold rounded-lg transition-all ${view === 'playground' ? 'bg-[#1e1f29] text-white shadow-md' : 'text-gray-400 hover:text-gray-200'}`}
          >
            LLM Playground
          </button>
        </nav>

        {/* Health status indicator */}
        <div className="flex items-center space-x-2">
          {healthLoading ? (
            <span className="h-2 w-2 rounded-full bg-yellow-500 animate-ping" />
          ) : health ? (
            <div className="flex items-center space-x-2 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1 rounded-full text-xs text-emerald-400 font-semibold">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-duration-1000"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
              <span>Online</span>
            </div>
          ) : (
            <div className="bg-rose-500/10 border border-rose-500/20 px-3 py-1 rounded-full text-xs text-rose-400 font-semibold">
              Offline
            </div>
          )}
        </div>
      </header>

      {/* Main Container */}
      <main className="flex-1 p-6 max-w-7xl w-full mx-auto relative z-10">

        {/* VIEW 1: Dashboard Home */}
        {view === 'home' && (
          <div className="space-y-8 animate-fadeIn">
            {/* Greeting & Headline */}
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
              <div>
                <h2 className="text-2xl font-black text-white">Project Workspaces</h2>
                <p className="text-sm text-gray-400">Launch autonomous agent pipelines to build your applications</p>
              </div>
              <button 
                onClick={fetchProjects}
                className="bg-white/5 border border-white/10 hover:bg-white/10 p-2.5 rounded-xl transition-all"
                title="Refresh project list"
              >
                <svg className="w-4 h-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H17.5"></path>
                </svg>
              </button>
            </div>

            {/* Layout Grid: Projects List & Create Project panel */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              
              {/* Left Column: Create Project Form */}
              <div className="lg:col-span-1">
                <div className="bg-[#0f1016]/90 border border-white/10 rounded-2xl p-6 shadow-2xl space-y-4 backdrop-blur-xl">
                  <h3 className="text-base font-extrabold text-white flex items-center space-x-2">
                    <span className="h-2 w-2 rounded-full bg-purple-500 animate-pulse" />
                    <span>Create New Agent Build</span>
                  </h3>
                  <p className="text-xs text-gray-400 leading-relaxed">
                    Submit your application idea. The AI agent pipeline will autonomously research, design, structure, code, test, and containerize it.
                  </p>

                  <form onSubmit={handleCreateProject} className="space-y-4 pt-2">
                    <div className="space-y-1">
                      <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest block">Project Name</label>
                      <input 
                        type="text" 
                        required
                        value={newProjectName}
                        onChange={(e) => setNewProjectName(e.target.value)}
                        placeholder="e.g. ChatClient, TodoPro"
                        className="w-full bg-[#181922] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-purple-500 transition-colors font-medium"
                      />
                    </div>

                    <div className="space-y-1">
                      <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest block">Idea & Brief</label>
                      <textarea 
                        required
                        value={newProjectBrief}
                        onChange={(e) => setNewProjectBrief(e.target.value)}
                        placeholder="Describe the application features, tech stack preferences, layout details..."
                        rows={4}
                        className="w-full bg-[#181922] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-purple-500 transition-colors font-medium resize-none"
                      />
                    </div>

                    {createError && (
                      <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-xs font-mono">
                        {createError}
                      </div>
                    )}

                    <button 
                      type="submit"
                      disabled={createLoading}
                      className="w-full bg-gradient-to-r from-purple-600 via-indigo-600 to-blue-600 hover:opacity-95 text-white py-2.5 rounded-xl text-xs font-bold tracking-wider uppercase transition-all shadow-lg shadow-purple-500/10 flex items-center justify-center space-x-2 cursor-pointer"
                    >
                      {createLoading ? (
                        <>
                          <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                          </svg>
                          <span>Initializing Pipeline...</span>
                        </>
                      ) : (
                        <span>Spawn Agents</span>
                      )}
                    </button>
                  </form>
                </div>
              </div>

              {/* Right Column: Workspaces List */}
              <div className="lg:col-span-2 space-y-4">
                {projectsLoading ? (
                  <div className="flex flex-col items-center justify-center py-20 space-y-3">
                    <svg className="animate-spin h-8 w-8 text-indigo-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    <span className="text-xs text-gray-500">Loading workspaces...</span>
                  </div>
                ) : projectsError ? (
                  <div className="bg-rose-500/10 border border-rose-500/20 text-rose-400 p-6 rounded-2xl text-center space-y-2">
                    <p className="text-sm font-bold">Failed to load workspaces</p>
                    <p className="text-xs font-mono">{projectsError}</p>
                    <button onClick={fetchProjects} className="px-4 py-1.5 bg-rose-500/20 rounded-lg text-xs font-bold hover:bg-rose-500/30">Retry</button>
                  </div>
                ) : projects.length === 0 ? (
                  <div className="bg-[#0f1016]/50 border border-white/5 rounded-2xl py-24 px-6 text-center space-y-3">
                    <div className="mx-auto h-12 w-12 text-gray-600 bg-white/5 rounded-full flex items-center justify-center">
                      <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"></path>
                      </svg>
                    </div>
                    <div className="space-y-1">
                      <h4 className="text-sm font-bold text-gray-300">No active workspaces</h4>
                      <p className="text-xs text-gray-500 max-w-sm mx-auto">Submit the build form on the left to initialize your first multi-agent software build.</p>
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {projects.map((project) => (
                      <div 
                        key={project.id}
                        onClick={() => {
                          setSelectedProjectId(project.id)
                          setView('detail')
                        }}
                        className="bg-[#0f1016]/80 border border-white/5 hover:border-purple-500/20 rounded-2xl p-5 shadow-lg transition-all duration-300 group cursor-pointer flex flex-col justify-between hover:-translate-y-0.5"
                      >
                        <div className="space-y-2">
                          <div className="flex items-center justify-between">
                            <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold ${getStatusBadgeClass(project.status)}`}>
                              {getStatusLabel(project.status)}
                            </span>
                            <span className="text-[10px] text-gray-500 font-mono">{new Date(project.created_at).toLocaleDateString()}</span>
                          </div>
                          
                          <h4 className="text-base font-extrabold text-white group-hover:text-purple-400 transition-colors">
                            {project.name}
                          </h4>
                          
                          <p className="text-xs text-gray-400 line-clamp-2 leading-relaxed">
                            {project.brief}
                          </p>
                        </div>

                        <div className="flex items-center justify-between pt-4 mt-4 border-t border-white/5 text-[11px]">
                          <span className="text-gray-500">
                            Stage: <strong className="text-gray-300 font-bold uppercase tracking-wider font-mono text-[9px]">{project.current_stage || 'N/A'}</strong>
                          </span>
                          <span className="text-purple-400 group-hover:translate-x-0.5 transition-transform flex items-center space-x-1 font-semibold">
                            <span>Open Workspace</span>
                            <span>→</span>
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

            </div>
          </div>
        )}

        {/* VIEW 2: Project Detail / Active Run Workspace */}
        {view === 'detail' && (
          <div className="space-y-6 animate-fadeIn">
            {/* Detail Navigation Header */}
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
              <button 
                onClick={() => setView('home')}
                className="text-xs font-semibold text-gray-400 hover:text-white flex items-center space-x-1.5 transition-colors group"
              >
                <span className="group-hover:-translate-x-0.5 transition-transform">←</span>
                <span>Back to Dashboard</span>
              </button>
              
              {projectDetail && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-gray-500 font-mono">Workspace ID: {projectDetail.project_id}</span>
                  <span className={`px-3 py-1 rounded-full text-xs font-bold ${getStatusBadgeClass(projectDetail.status)}`}>
                    {getStatusLabel(projectDetail.status)}
                  </span>
                  {wsStatus === 'connected' ? (
                    <span className="px-2 py-0.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-md text-[10px] font-bold flex items-center space-x-1.5">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-ping" />
                      <span>Live Connection</span>
                    </span>
                  ) : (
                    <span className="px-2 py-0.5 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-md text-[10px] font-bold">
                      {wsStatus === 'connecting' ? 'Connecting Live...' : 'No Connection'}
                    </span>
                  )}
                </div>
              )}
            </div>

            {/* Error alerts */}
            {detailError && (
              <div className="bg-rose-500/10 border border-rose-500/20 text-rose-400 p-4 rounded-xl text-xs font-mono">
                <strong>Error fetching snapshot:</strong> {detailError}
              </div>
            )}

            {projectDetail && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                
                {/* 2/3 COLUMN: Main workspace deliverables & logs */}
                <div className="lg:col-span-2 space-y-6">
                  
                  {/* Top card showing general project description */}
                  <div className="bg-[#0f1016]/80 border border-white/5 rounded-2xl p-6 shadow-xl space-y-2">
                    <h2 className="text-2xl font-black text-white">{projectDetail.project_name}</h2>
                    <p className="text-sm text-gray-300 leading-relaxed">{projectDetail.brief}</p>
                  </div>

                  {/* Horizontal pipeline stage progress visualization */}
                  <div className="bg-[#0f1016]/60 border border-white/5 rounded-2xl p-6 shadow-xl space-y-4">
                    <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">Pipeline Stages</h3>
                    
                    <div className="flex flex-wrap items-center gap-2">
                      {PIPELINE_STAGES.map((stage, idx) => {
                        const currentStageIdx = PIPELINE_STAGES.indexOf(projectDetail.current_stage)
                        const isCompleted = currentStageIdx > idx || projectDetail.status === 'completed'
                        const isActive = projectDetail.current_stage === stage && projectDetail.status === 'running'
                        
                        let stateClass = 'bg-[#181922] border-white/5 text-gray-500'
                        if (isCompleted) {
                          stateClass = 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400 font-bold'
                        } else if (isActive) {
                          stateClass = 'bg-blue-500/10 border-blue-500/40 text-blue-400 font-bold shadow-[0_0_12px_rgba(59,130,246,0.1)]'
                        }

                        return (
                          <div key={stage} className="flex items-center space-x-1">
                            <span className={`px-2.5 py-1 text-xs border rounded-lg uppercase font-mono tracking-wider transition-colors ${stateClass}`}>
                              {stage.replace('_', ' ')}
                            </span>
                            {idx < PIPELINE_STAGES.length - 1 && (
                              <span className="text-gray-700 text-xs">→</span>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  {/* Document and generated files viewing tabbed interface */}
                  <div className="bg-[#0f1016]/90 border border-white/5 rounded-2xl shadow-xl overflow-hidden flex flex-col min-h-[500px]">
                    {/* Tab Navigation header */}
                    <div className="border-b border-white/5 bg-white/5 p-2 flex items-center justify-between overflow-x-auto gap-2">
                      <div className="flex items-center space-x-1">
                        {[
                          { id: 'research', label: 'Research Report' },
                          { id: 'requirements', label: 'Requirements' },
                          { id: 'architecture', label: 'Architecture & Design' },
                          { id: 'plan', label: 'Implementation Plan' },
                          { id: 'code', label: 'Generated Files' },
                          { id: 'qa', label: 'QA Report' }
                        ].map(tab => (
                          <button
                            key={tab.id}
                            onClick={() => setActiveDocTab(tab.id)}
                            className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${activeDocTab === tab.id ? 'bg-[#181922] text-purple-400 border border-purple-500/10' : 'text-gray-400 hover:text-gray-200'}`}
                          >
                            {tab.label}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Tab contents */}
                    <div className="p-6 flex-1 flex flex-col">
                      
                      {/* TAB: Research */}
                      {activeDocTab === 'research' && (
                        <div className="prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed space-y-4">
                          {projectDetail.research_report ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{projectDetail.research_report}</ReactMarkdown>
                          ) : (
                            <p className="text-xs text-gray-500 font-mono italic">Research report has not been generated yet. It will appear once the Research agent finishes.</p>
                          )}
                        </div>
                      )}

                      {/* TAB: Requirements */}
                      {activeDocTab === 'requirements' && (
                        <div className="prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed space-y-4">
                          {projectDetail.requirements_doc ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{projectDetail.requirements_doc}</ReactMarkdown>
                          ) : (
                            <p className="text-xs text-gray-500 font-mono italic">Requirements document has not been generated yet.</p>
                          )}
                        </div>
                      )}

                      {/* TAB: Architecture */}
                      {activeDocTab === 'architecture' && (
                        <div className="space-y-6">
                          {projectDetail.tech_stack && (
                            <div className="space-y-1">
                              <h4 className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Tech Stack Selection</h4>
                              <div className="p-4 bg-[#14151b] border border-white/5 rounded-xl text-sm font-mono whitespace-pre-wrap text-[#c084fc]">
                                {projectDetail.tech_stack}
                              </div>
                            </div>
                          )}
                          <div className="prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed space-y-2">
                            {projectDetail.architecture_doc ? (
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{projectDetail.architecture_doc}</ReactMarkdown>
                            ) : (
                              <p className="text-xs text-gray-500 font-mono italic">Architecture design has not been generated yet.</p>
                            )}
                          </div>
                        </div>
                      )}

                      {/* TAB: Implementation Plan */}
                      {activeDocTab === 'plan' && (
                        <div className="prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed space-y-4">
                          {projectDetail.implementation_plan ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{projectDetail.implementation_plan}</ReactMarkdown>
                          ) : (
                            <p className="text-xs text-gray-500 font-mono italic">Implementation plan has not been generated yet.</p>
                          )}
                        </div>
                      )}

                      {/* TAB: QA Report */}
                      {activeDocTab === 'qa' && (
                        <div className="prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed space-y-4">
                          {projectDetail.qa_report ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{projectDetail.qa_report}</ReactMarkdown>
                          ) : (
                            <p className="text-xs text-gray-500 font-mono italic">QA Report has not been generated yet.</p>
                          )}
                        </div>
                      )}

                      {/* TAB: Code Files */}
                      {activeDocTab === 'code' && (
                        <div className="flex-1 flex flex-col md:flex-row gap-6">
                          {/* File list side column */}
                          <div className="w-full md:w-56 border-r border-white/5 pr-4 flex flex-col gap-2">
                            <h4 className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">File Workspace</h4>
                            {projectDetail.file_list && projectDetail.file_list.length > 0 ? (
                              <div className="flex flex-col gap-1 overflow-y-auto max-h-[400px]">
                                {projectDetail.file_list.map(filepath => (
                                  <button
                                    key={filepath}
                                    onClick={() => setSelectedCodeFile(filepath)}
                                    className={`px-3 py-1.5 text-xs text-left rounded-lg transition-colors truncate font-mono ${selectedCodeFile === filepath ? 'bg-purple-600/10 text-purple-400 border border-purple-500/20 font-bold' : 'text-gray-400 hover:text-gray-200'}`}
                                  >
                                    {filepath.split('/').pop()}
                                    <span className="block text-[8px] text-gray-500 select-all">{filepath}</span>
                                  </button>
                                ))}
                              </div>
                            ) : (
                              <p className="text-[10px] text-gray-600 italic">No files generated yet.</p>
                            )}
                          </div>

                          {/* File Content Preview Panel */}
                          <div className="flex-1 flex flex-col">
                            {selectedCodeFile ? (
                              <div className="flex-1 flex flex-col space-y-2">
                                <div className="flex justify-between items-center bg-[#14151b] px-4 py-2 border border-white/5 rounded-t-xl">
                                  <span className="text-xs font-mono text-gray-300 font-bold truncate">{selectedCodeFile}</span>
                                </div>
                                <pre className="flex-1 bg-[#14151b] border border-white/5 border-t-0 p-4 text-xs font-mono text-gray-300 rounded-b-xl overflow-x-auto max-h-[500px]">
                                  {projectDetail.generated_files?.[selectedCodeFile] || '// Empty file or content not available.'}
                                </pre>
                              </div>
                            ) : (
                              <div className="flex-1 flex items-center justify-center border border-dashed border-white/10 rounded-xl py-24">
                                <span className="text-xs text-gray-600 font-mono italic">Select a file on the left to preview code.</span>
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                    </div>
                  </div>

                </div>

                {/* 1/3 COLUMN: Active socket logging events and gates */}
                <div className="lg:col-span-1 space-y-6">
                  
                  {/* Gate Controller Form */}
                  {projectDetail.status === 'awaiting_approval' && (
                    <div className="bg-gradient-to-b from-[#1c1d29] to-[#12131b] border border-amber-500/20 rounded-2xl p-6 shadow-2xl space-y-4 animate-scaleUp">
                      <div className="flex items-center space-x-2 border-b border-amber-500/10 pb-3">
                        <div className="p-2 bg-amber-500/10 border border-amber-500/20 rounded-lg">
                          <svg className="w-5 h-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path>
                          </svg>
                        </div>
                        <div>
                          <h4 className="text-sm font-black text-amber-400">Human Approval Required</h4>
                          <span className="text-[10px] text-gray-400 font-mono">Gate paused at {projectDetail.next_gate || 'gate'}</span>
                        </div>
                      </div>

                      <div className="space-y-1">
                        <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest block">Instructions / Feedback</label>
                        <textarea
                          placeholder="Provide instructions to edit the requirements, approve, or reject..."
                          value={feedbackText}
                          onChange={(e) => setFeedbackText(e.target.value)}
                          rows={3}
                          className="w-full bg-[#181922] border border-white/10 rounded-xl px-4 py-2.5 text-xs text-white focus:outline-none focus:border-amber-500 transition-colors font-medium resize-none"
                        />
                      </div>

                      {resumeError && (
                        <div className="p-3 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-xs font-mono">
                          {resumeError}
                        </div>
                      )}

                      <div className="flex flex-col gap-2 pt-2">
                        {submittingResume ? (
                          <div className="flex justify-center py-2">
                            <svg className="animate-spin h-5 w-5 text-amber-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                          </div>
                        ) : (
                          <>
                            <button
                              onClick={() => handleResumeGate('approve')}
                              className="w-full bg-gradient-to-r from-emerald-600 to-green-600 hover:from-emerald-500 hover:to-green-500 text-white py-2 rounded-xl text-xs font-bold uppercase transition-all shadow-md cursor-pointer"
                            >
                              Approve & Continue
                            </button>
                            <button
                              onClick={() => handleResumeGate('edit')}
                              className="w-full bg-[#27201c] border border-amber-600/30 hover:bg-[#2c241f] text-amber-400 py-2 rounded-xl text-xs font-bold uppercase transition-all cursor-pointer"
                            >
                              Request Changes (Edit)
                            </button>
                            <button
                              onClick={() => handleResumeGate('reject')}
                              className="w-full bg-[#241c1d] border border-rose-600/30 hover:bg-[#2c1f20] text-rose-400 py-2 rounded-xl text-xs font-bold uppercase transition-all cursor-pointer"
                            >
                              Reject & Terminate
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Errors display */}
                  {projectDetail.errors && projectDetail.errors.length > 0 && (
                    <div className="bg-rose-500/10 border border-rose-500/20 text-rose-400 p-5 rounded-2xl space-y-2">
                      <h4 className="text-xs font-bold uppercase tracking-wider flex items-center space-x-1">
                        <span>⚠️ Execution Failures</span>
                      </h4>
                      <div className="text-xs font-mono max-h-32 overflow-y-auto space-y-1.5">
                        {projectDetail.errors.map((err, idx) => (
                          <div key={idx} className="border-t border-rose-500/10 pt-1.5 first:border-0 first:pt-0">
                            {err}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* WebSocket Live Stream events widget */}
                  <div className="bg-[#0f1016]/90 border border-white/5 rounded-2xl p-6 shadow-xl flex flex-col h-[500px]">
                    <div className="flex items-center justify-between border-b border-white/5 pb-3 mb-4">
                      <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center space-x-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-purple-500 animate-ping" />
                        <span>Live Activity Feed</span>
                      </h3>
                      <span className="text-[10px] text-gray-600 font-mono">{wsEvents.length} events</span>
                    </div>

                    {/* Events list */}
                    <div className="flex-1 overflow-y-auto space-y-3 pr-2 scrollbar-thin">
                      {wsEvents.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-gray-600 text-xs italic font-mono">
                          Waiting for events to broadcast...
                        </div>
                      ) : (
                        [...wsEvents].reverse().map((event, idx) => {
                          let cardClass = 'bg-[#181922] border-white/5'
                          let iconColor = 'text-gray-400'
                          let iconPath = 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'

                          if (event.type === 'agent_complete') {
                            cardClass = 'bg-purple-950/10 border-purple-500/10'
                            iconColor = 'text-purple-400'
                            iconPath = 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'
                          } else if (event.type === 'gate_reached') {
                            cardClass = 'bg-amber-950/10 border-amber-500/10'
                            iconColor = 'text-amber-400'
                            iconPath = 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z'
                          } else if (event.type === 'pipeline_complete') {
                            cardClass = 'bg-emerald-950/10 border-emerald-500/10 shadow-[0_0_15px_rgba(16,185,129,0.05)]'
                            iconColor = 'text-emerald-400'
                            iconPath = 'M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z'
                          } else if (event.type === 'error') {
                            cardClass = 'bg-rose-950/10 border-rose-500/10'
                            iconColor = 'text-rose-400'
                            iconPath = 'M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
                          }

                          return (
                            <div key={idx} className={`p-3.5 border rounded-xl space-y-1.5 transition-all text-xs ${cardClass} animate-slideIn`}>
                              <div className="flex items-center justify-between">
                                <div className="flex items-center space-x-1.5">
                                  <svg className={`h-4.5 w-4.5 ${iconColor}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={iconPath}></path>
                                  </svg>
                                  <span className="font-extrabold uppercase tracking-wider text-[10px]">
                                    {event.type === 'agent_complete' ? `${event.agent} agent completed` : event.type.replace('_', ' ')}
                                  </span>
                                </div>
                                <span className="text-[9px] text-gray-500 font-mono">{event.timestamp}</span>
                              </div>
                              
                              {event.preview && (
                                <p className="text-gray-400 font-mono text-[11px] leading-relaxed break-words bg-[#08090d]/50 p-2 rounded-lg border border-white/5">
                                  {event.preview}
                                </p>
                              )}
                              {event.message && (
                                <p className="text-rose-400 font-mono text-[11px] leading-relaxed break-words bg-rose-950/10 p-2 rounded-lg border border-rose-500/10">
                                  {event.message}
                                </p>
                              )}
                            </div>
                          )
                        })
                      )}
                    </div>
                  </div>

                </div>

              </div>
            )}
          </div>
        )}

        {/* VIEW 3: LLM Router Playground (Day 2 view preserved) */}
        {view === 'playground' && (
          <div className="max-w-2xl mx-auto space-y-6 animate-fadeIn">
            <div>
              <h2 className="text-xl font-bold text-white">LLM Router Playground</h2>
              <p className="text-xs text-gray-400">Directly test agent prompt assignments and router selection</p>
            </div>

            <div className="bg-[#0f1016]/90 border border-white/10 rounded-2xl p-6 shadow-2xl">
              <form onSubmit={handleTestLLM} className="space-y-4">
                {/* Agent Selector */}
                <div>
                  <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                    Agent Type (Assigned LLMs)
                  </label>
                  <select
                    value={agentType}
                    onChange={(e) => setAgentType(e.target.value)}
                    className="w-full bg-[#181922] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-gray-200 focus:outline-none focus:border-purple-500 transition-colors font-semibold"
                  >
                    {AGENT_TYPES.map((agent) => (
                      <option key={agent.value} value={agent.value}>
                        {agent.label}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Prompt Input */}
                <div>
                  <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                    Prompt
                  </label>
                  <textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    rows={3}
                    className="w-full bg-[#181922] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-gray-200 focus:outline-none focus:border-purple-500 focus:ring-1 focus:ring-purple-500/20 transition-all resize-none font-medium"
                    placeholder="Enter prompt to send to the router..."
                  />
                </div>

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={testLoading}
                  className="w-full bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 disabled:opacity-50 text-white text-xs font-bold uppercase tracking-wider py-2.5 px-4 rounded-xl shadow-lg transition-all duration-200 flex items-center justify-center space-x-2 cursor-pointer"
                >
                  {testLoading ? (
                    <>
                      <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                      </svg>
                      <span>Querying Router...</span>
                    </>
                  ) : (
                    <span>Test LLM Router</span>
                  )}
                </button>
              </form>

              {/* Results section */}
              {(testResult || testError) && (
                <div className="mt-5 space-y-2">
                  <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider">
                    Output
                  </label>
                  
                  {testError && (
                    <div className="p-4 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-xs font-mono">
                      <span className="font-bold">Error:</span> {testError}
                    </div>
                  )}

                  {testResult && (
                    <div className="p-4 bg-[#14151b] border border-white/5 text-gray-200 rounded-xl text-sm font-mono whitespace-pre-wrap leading-relaxed shadow-inner">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{testResult}</ReactMarkdown>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

      </main>

      {/* Footer */}
      <footer className="border-t border-white/5 bg-[#0a0b10] px-6 py-4 flex flex-col md:flex-row justify-between items-center text-xs text-gray-600 gap-2">
        <span>Day 4 Setup Complete • Live WebSockets streaming and SQLite state tracking</span>
        <span>Multi-Agent AI Builder Platform</span>
      </footer>
    </div>
  )
}

export default App
