import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'

export default function NewProjectPage() {
  const navigate = useNavigate()
  
  const [projectName, setProjectName] = useState('')
  const [projectBrief, setProjectBrief] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    // Validate fields
    if (!projectName.trim()) {
      setError('Project Name is required.')
      return
    }
    if (!projectBrief.trim()) {
      setError('Project Brief is required.')
      return
    }

    setLoading(true)
    setError('')

    try {
      const res = await fetch('http://localhost:8000/api/projects', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          project_name: projectName,
          brief: projectBrief,
        }),
      })

      if (!res.ok) {
        throw new Error('Server responded with an error')
      }

      const data = await res.json()
      if (data && data.project_id) {
        // Redirect to detail page
        navigate(`/projects/${data.project_id}`)
      } else {
        throw new Error('No project_id returned in response')
      }
    } catch (err) {
      console.error(err)
      setError('Failed to start project. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-64px)] p-6 bg-[#f9fafb]">
      <div className="w-full max-w-2xl bg-white rounded-lg border border-gray-200 shadow-sm p-8">
        <h1 className="text-2xl font-bold text-gray-900 mb-6 text-center">
          Start a New Project
        </h1>

        <div className="space-y-6">
          {/* Project Name Field */}
          <div>
            <label className="block text-sm font-semibold text-gray-700 mb-2">
              Project Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={projectName}
              onChange={(e) => {
                setProjectName(e.target.value)
                setError('')
              }}
              placeholder="e.g. TaskForge, SlackClone, DevDocs"
              className="w-full px-4 py-2 border border-gray-300 rounded focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none text-gray-900 placeholder-gray-400 text-sm transition-colors"
              disabled={loading}
            />
          </div>

          {/* Project Brief Field */}
          <div>
            <label className="block text-sm font-semibold text-gray-700 mb-2">
              Project Brief <span className="text-red-500">*</span>
            </label>
            <textarea
              rows={12}
              value={projectBrief}
              onChange={(e) => {
                setProjectBrief(e.target.value)
                setError('')
              }}
              placeholder="Describe what you're building, who it's for, the key features (max 8), any technical constraints, and what to leave out. The more specific you are, the better the agents perform."
              className="w-full px-4 py-2 border border-gray-300 rounded focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none text-gray-900 placeholder-gray-400 text-sm transition-colors font-sans"
              disabled={loading}
            />
            <p className="mt-2 text-xs text-gray-500 leading-relaxed">
              Tip: include your target users, core features, and any tech preferences. Avoid vague one-liners.
            </p>
          </div>

          {/* Error Message */}
          {error && (
            <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded text-sm text-red-700">
              {error}
            </div>
          )}

          {/* Submit Button */}
          <div className="pt-2">
            <button
              onClick={handleSubmit}
              disabled={loading}
              className={`w-full font-semibold py-2.5 px-4 rounded text-white text-sm transition-colors cursor-pointer text-center block ${
                loading
                  ? 'bg-blue-400 cursor-not-allowed'
                  : 'bg-blue-600 hover:bg-blue-700'
              }`}
            >
              {loading ? 'Starting...' : 'Start Building →'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
