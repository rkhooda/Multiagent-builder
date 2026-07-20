import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'

const OPTIONAL_SECTION_CONFIG = [
  {
    key: 'existing_solutions',
    label: 'Existing Solutions & Competitors',
    description: 'Include a competitive analysis table with real market alternatives',
  },
  {
    key: 'target_users',
    label: 'Target Users',
    description: 'Include a detailed user persona with pain points and current workarounds',
  },
  {
    key: 'market_risks',
    label: 'Market Risks',
    description: 'Include market-specific risks like competition, adoption, and pricing pressure',
  },
]

export default function NewProjectPage() {
  const navigate = useNavigate()

  const [projectName, setProjectName] = useState('')
  const [projectBrief, setProjectBrief] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [fastMode, setFastMode] = useState(false)
  const [optionalSections, setOptionalSections] = useState({
    existing_solutions: false,
    target_users: false,
    market_risks: false,
  })

  const toggleSection = (key) => {
    setOptionalSections((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const handleSubmit = async () => {
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
          fast_mode: fastMode,
          optional_sections: {
            existing_solutions: optionalSections.existing_solutions,
            target_users: optionalSections.target_users,
            market_risks: optionalSections.market_risks,
          },
        }),
      })

      if (!res.ok) {
        throw new Error('Server responded with an error')
      }

      const data = await res.json()
      if (data && data.project_id) {
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
    <div className="flex items-center justify-center min-h-[calc(100vh-64px)] p-6 bg-surface">
      <div className="w-full max-w-2xl bg-raised rounded-lg border border-line  p-8">
        <h1 className="text-2xl font-bold text-ink mb-6 text-center">
          Start a New Project
        </h1>

        <div className="space-y-6">
          {/* Project Name Field */}
          <div>
            <label className="block text-sm font-semibold text-ink mb-2">
              Project Name <span className="text-err">*</span>
            </label>
            <input
              type="text"
              value={projectName}
              onChange={(e) => {
                setProjectName(e.target.value)
                setError('')
              }}
              placeholder="e.g. TaskForge, SlackClone, DevDocs"
              className="w-full px-4 py-2 border border-line-strong rounded focus:ring-1 focus:ring-accent focus:border-accent outline-none text-ink placeholder-ink-3 text-sm transition-colors"
              disabled={loading}
            />
          </div>

          {/* Project Brief Field */}
          <div>
            <label className="block text-sm font-semibold text-ink mb-2">
              Project Brief <span className="text-err">*</span>
            </label>
            <textarea
              rows={12}
              value={projectBrief}
              onChange={(e) => {
                setProjectBrief(e.target.value)
                setError('')
              }}
              placeholder="Describe what you're building, who it's for, the key features (max 8), any technical constraints, and what to leave out. The more specific you are, the better the agents perform."
              className="w-full px-4 py-2 border border-line-strong rounded focus:ring-1 focus:ring-accent focus:border-accent outline-none text-ink placeholder-ink-3 text-sm transition-colors font-sans"
              disabled={loading}
            />
            <p className="mt-2 text-xs text-ink-3 leading-relaxed">
              Tip: include your target users, core features, and any tech preferences. Avoid vague one-liners.
            </p>
          </div>

          {/* Research Sections */}
          <div>
            <p className="text-sm font-semibold text-ink mb-1">Research Sections</p>
            <p className="text-xs text-ink-3 mb-3">
              The following sections are always included: Problem Space, Technical Landscape,
              Execution Risks, Recommended Approach, Confidence Score. Select any additional
              sections you need:
            </p>
            <div className="bg-overlay border border-line rounded-lg p-4 space-y-4">
              {OPTIONAL_SECTION_CONFIG.map(({ key, label, description }) => (
                <label
                  key={key}
                  className="flex items-start gap-3 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={optionalSections[key]}
                    onChange={() => toggleSection(key)}
                    disabled={loading}
                    className="mt-0.5 h-4 w-4 rounded border-line-strong accent-accent focus:ring-accent cursor-pointer flex-shrink-0"
                  />
                  <div>
                    <span className="block text-sm font-medium text-ink">{label}</span>
                    <span className="block text-xs text-ink-3 mt-0.5">{description}</span>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Fast Mode */}
          <div>
            <p className="text-sm font-semibold text-ink mb-1">Generation Mode</p>
            <div className="bg-overlay border border-line rounded-lg p-4">
              <label className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={fastMode}
                  onChange={() => setFastMode((v) => !v)}
                  disabled={loading}
                  className="mt-0.5 h-4 w-4 rounded border-line-strong accent-accent focus:ring-accent cursor-pointer flex-shrink-0"
                />
                <div>
                  <span className="block text-sm font-medium text-ink">Fast Mode</span>
                  <span className="block text-xs text-ink-3 mt-0.5">
                    Faster, lighter outputs &mdash; good for quick iteration and testing,
                    not final builds. Generated files are still checked for syntax and
                    import errors, but problems are reported rather than automatically
                    repaired.
                  </span>
                </div>
              </label>
            </div>
          </div>

          {/* Error Message */}
          {error && (
            <div className="bg-err/10 border-l-4 border-err p-4 rounded text-sm text-err">
              {error}
            </div>
          )}

          {/* Submit Button */}
          <div className="pt-2">
            <button
              onClick={handleSubmit}
              disabled={loading}
              className={`w-full font-semibold py-2.5 px-4 rounded text-ink text-sm transition-colors cursor-pointer text-center block ${
                loading
                  ? 'bg-run cursor-not-allowed'
                  : 'bg-overlay hover:bg-line'
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
