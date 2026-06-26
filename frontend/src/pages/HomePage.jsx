import React, { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

export default function HomePage() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchProjects = async () => {
    try {
      setLoading(true)
      const res = await fetch('http://localhost:8000/api/projects')
      if (!res.ok) {
        throw new Error('Failed to retrieve projects list')
      }
      const data = await res.json()
      setProjects(data)
    } catch (err) {
      console.error(err)
      setError('Could not connect to the backend. Is it running?')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchProjects()
  }, [])

  const formatDate = (isoString) => {
    if (!isoString) return ''
    try {
      const date = new Date(isoString)
      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
      })
    } catch (e) {
      return isoString
    }
  }

  const getStatusBadgeStyle = (status) => {
    switch (status) {
      case 'completed':
      case 'complete':
        return 'bg-green-100 text-green-800 border-green-200'
      case 'awaiting_approval':
        return 'bg-orange-100 text-orange-800 border-orange-200'
      case 'running':
        return 'bg-blue-100 text-blue-800 border-blue-200'
      case 'error':
      case 'failed':
        return 'bg-red-100 text-red-800 border-red-200'
      default:
        return 'bg-gray-100 text-gray-800 border-gray-200'
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)] p-6 bg-[#f9fafb]">
        <div className="flex items-center space-x-3 text-gray-500 animate-pulse">
          <svg className="animate-spin h-6 w-6 text-blue-600" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          <span className="font-medium">Loading projects...</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[calc(100vh-64px)] p-6 bg-[#f9fafb] text-center space-y-4">
        <div className="text-red-500 text-4xl">⚠️</div>
        <h2 className="text-lg font-bold text-gray-900">Backend Connection Error</h2>
        <p className="text-sm text-gray-600">{error}</p>
        <button
          onClick={fetchProjects}
          className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded text-sm transition-colors cursor-pointer"
        >
          Try Again
        </button>
      </div>
    )
  }

  return (
    <div className="p-6 bg-[#f9fafb] min-h-[calc(100vh-64px)]">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex justify-between items-center border-b border-gray-200 pb-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Your Projects</h1>
            <p className="text-sm text-gray-500 mt-1">
              Autonomously build web applications with agent pipelines.
            </p>
          </div>
          <button
            onClick={() => navigate('/new')}
            className="bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded text-sm transition-colors cursor-pointer"
          >
            Start a New Project
          </button>
        </div>

        {projects.length === 0 ? (
          <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-12 text-center flex flex-col items-center max-w-xl mx-auto mt-12">
            <div className="text-5xl mb-4">🚀</div>
            <h3 className="text-lg font-bold text-gray-900 mb-2">No projects yet</h3>
            <p className="text-sm text-gray-600 mb-6 max-w-sm leading-relaxed">
              Submit your idea and let the pipeline of autonomous agents build it for you.
            </p>
            <button
              onClick={() => navigate('/new')}
              className="bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 px-5 rounded text-sm transition-colors cursor-pointer"
            >
              Create Your First Project &rarr;
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {projects.map((project) => (
              <div
                key={project.id}
                className="bg-white rounded-lg border border-gray-200 shadow-xs p-6 hover:shadow-sm transition-all duration-200 flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="text-base font-bold text-gray-900 truncate pr-2" title={project.name}>
                      {project.name}
                    </h3>
                    <span className={`px-2 py-0.5 text-[10px] font-semibold rounded-full border ${getStatusBadgeStyle(project.status)}`}>
                      {project.status.replace('_', ' ')}
                    </span>
                  </div>
                  <p className="text-sm text-gray-600 line-clamp-2 leading-relaxed mb-4">
                    {project.brief}
                  </p>
                </div>

                <div className="border-t border-gray-100 pt-4 flex justify-between items-center mt-2">
                  <span className="text-xs text-gray-500 font-medium">
                    {formatDate(project.created_at)}
                  </span>
                  <Link
                    to={`/projects/${project.id}`}
                    className="text-xs font-semibold text-blue-600 hover:text-blue-700 transition-colors inline-flex items-center"
                  >
                    View Project &rarr;
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
