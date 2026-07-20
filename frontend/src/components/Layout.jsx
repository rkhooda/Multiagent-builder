import React, { useState, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

export default function Layout({ children }) {
  const location = useLocation()
  const navigate = useNavigate()
  
  const [projects, setProjects] = useState([])
  const [isConnected, setIsConnected] = useState(false)
  const [checkingHealth, setCheckingHealth] = useState(true)

  // Determine page title based on path
  const getPageTitle = () => {
    const path = location.pathname
    if (path === '/') return 'Projects Dashboard'
    if (path === '/new') return 'Start a New Project'
    if (path.startsWith('/projects/')) {
      return 'Project Details'
    }
    return 'Agent Builder'
  }

  // Fetch health check
  const checkHealth = async () => {
    try {
      const res = await fetch('http://localhost:8000/health')
      if (res.ok) {
        setIsConnected(true)
      } else {
        setIsConnected(false)
      }
    } catch (err) {
      setIsConnected(false)
    } finally {
      setCheckingHealth(false)
    }
  }

  // Fetch projects for sidebar list
  const fetchProjects = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/projects')
      if (res.ok) {
        const data = await res.json()
        // The list endpoint returns {projects, total} as of Day 24.
        setProjects(data.projects || [])
      }
    } catch (err) {
      console.error('Error fetching projects for sidebar:', err)
    }
  }

  useEffect(() => {
    checkHealth()
    fetchProjects()

    // Refresh project list every 10 seconds
    const interval = setInterval(() => {
      fetchProjects()
    }, 10000)

    return () => clearInterval(interval)
  }, [])

  // Map backend status to dot colors
  const getStatusDotColor = (status) => {
    switch (status) {
      case 'completed':
      case 'complete':
        return 'bg-green-500'
      case 'awaiting_approval':
        return 'bg-orange-500'
      case 'running':
        return 'bg-blue-500'
      case 'error':
      case 'failed':
        return 'bg-red-500'
      default:
        return 'bg-gray-400'
    }
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden font-sans">
      {/* Left Sidebar */}
      <div className="w-[260px] bg-[#0d1117] text-gray-300 flex flex-col flex-shrink-0 h-full border-r border-gray-800">
        {/* App Title */}
        <div className="p-6 border-b border-gray-800">
          <Link to="/" className="text-xl font-bold text-white tracking-wide block hover:text-gray-100 transition-colors">
            🤖 Agent Builder
          </Link>
        </div>

        {/* New Project Button */}
        <div className="p-4">
          <button
            onClick={() => navigate('/new')}
            className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded transition-colors text-center block cursor-pointer"
          >
            + New Project
          </button>
        </div>

        {/* Recent Projects List */}
        <div className="flex-1 overflow-y-auto px-4 py-2">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 px-2">
            Recent Projects
          </h3>
          {projects.length === 0 ? (
            <p className="text-sm text-gray-500 px-2 italic">No projects yet</p>
          ) : (
            <ul className="space-y-1">
              {projects.map((project) => {
                const isActive = location.pathname === `/projects/${project.id}`
                return (
                  <li key={project.id}>
                    <Link
                      to={`/projects/${project.id}`}
                      className={`flex items-center justify-between px-3 py-2 rounded text-sm transition-colors ${
                        isActive
                          ? 'bg-gray-800 text-white font-medium'
                          : 'hover:bg-gray-900 text-gray-400 hover:text-gray-200'
                      }`}
                    >
                      <span className="truncate mr-2">{project.name}</span>
                      <span
                        className={`h-2.5 w-2.5 rounded-full flex-shrink-0 ${getStatusDotColor(
                          project.status
                        )}`}
                        title={project.status}
                      />
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </div>

      {/* Main Container */}
      <div className="flex-1 flex flex-col min-w-0 bg-[#f9fafb]">
        {/* Top Header Bar */}
        <header className="h-16 bg-[#161b22] text-white flex items-center justify-between px-6 border-b border-gray-800 flex-shrink-0 z-10 shadow-sm">
          <h2 className="text-lg font-semibold truncate">{getPageTitle()}</h2>
          
          {/* Health Indicator */}
          <div className="flex items-center space-x-2 text-sm">
            <span className="text-gray-400">Status:</span>
            {checkingHealth ? (
              <span className="inline-flex items-center text-gray-400">
                <svg className="animate-spin -ml-1 mr-1.5 h-4.5 w-4.5 text-gray-400" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                Checking...
              </span>
            ) : isConnected ? (
              <span className="inline-flex items-center text-green-400 font-medium">
                <span className="h-2 w-2 rounded-full bg-green-400 mr-1.5 animate-pulse" />
                Connected
              </span>
            ) : (
              <span className="inline-flex items-center text-red-400 font-medium">
                <span className="h-2 w-2 rounded-full bg-red-400 mr-1.5" />
                Disconnected
              </span>
            )}
          </div>
        </header>

        {/* Content Area */}
        <main className="flex-1 overflow-auto">
          {children}
        </main>
      </div>
    </div>
  )
}
