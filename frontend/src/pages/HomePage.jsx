import React, { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { RestartDialog, DeleteDialog } from '../components/LifecycleDialogs'
import { STATUS, statusMeta, stageLabel, formatDate, formatRelative } from '../lib/status'

const API = 'http://localhost:8000/api/projects'

// Filter options mirror the canonical backend vocabulary; `interrupted` is
// derived per row rather than stored, so it is not a server-side filter.
const FILTERS = [
  ['', 'All'],
  ['awaiting_approval', 'Awaiting approval'],
  ['running', 'Running'],
  ['completed', 'Complete'],
  ['error_paused', 'Error'],
  ['cancelled', 'Cancelled'],
]

const SORTS = [
  ['created_at', 'Newest first'],
  ['updated_at', 'Recently active'],
  ['name', 'Name (A–Z)'],
  ['status', 'Status'],
]

function StatusBadge({ status, interrupted }) {
  const meta = statusMeta(status, interrupted)
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold ${meta.badge}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </span>
  )
}

/**
 * Status-appropriate row actions. The primary action is what the user most
 * likely wants next for a project in that state — Resume for a paused gate,
 * Open for a finished one, Retry for a failure — so the list is operable
 * without opening every project first.
 */
function primaryAction(project) {
  if (project.interrupted) return { label: 'Resume', to: `/projects/${project.id}`, className: 'bg-purple-600 hover:bg-purple-700' }
  switch (project.status) {
    case 'awaiting_approval':
      return { label: 'Review', to: `/projects/${project.id}`, className: 'bg-orange-600 hover:bg-orange-700' }
    case 'error_paused':
    case 'rate_limited':
      return { label: 'Retry', to: `/projects/${project.id}`, className: 'bg-red-600 hover:bg-red-700' }
    case 'running':
      return { label: 'Watch', to: `/projects/${project.id}`, className: 'bg-blue-600 hover:bg-blue-700' }
    default:
      return { label: 'Open', to: `/projects/${project.id}`, className: 'bg-gray-700 hover:bg-gray-800' }
  }
}

export default function HomePage() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState('created_at')
  const [restarting, setRestarting] = useState(null)
  const [deleting, setDeleting] = useState(null)

  const fetchProjects = useCallback(async () => {
    try {
      setLoading(true)
      const params = new URLSearchParams({ sort })
      if (filter) params.set('status', filter)
      const res = await fetch(`${API}?${params}`)
      if (!res.ok) throw new Error('Failed to retrieve projects list')
      const data = await res.json()
      setProjects(data.projects || [])
      setTotal(data.total || 0)
      setError('')
    } catch (err) {
      console.error(err)
      setError('Could not connect to the backend. Is it running?')
    } finally {
      setLoading(false)
    }
  }, [filter, sort])

  useEffect(() => { fetchProjects() }, [fetchProjects])

  if (error) {
    return (
      <div className="flex min-h-[calc(100vh-64px)] flex-col items-center justify-center space-y-4 bg-[#f9fafb] p-6 text-center">
        <div className="text-4xl">⚠️</div>
        <h2 className="text-lg font-bold text-gray-900">Backend Connection Error</h2>
        <p className="text-sm text-gray-600">{error}</p>
        <button
          onClick={fetchProjects}
          className="cursor-pointer rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          Try Again
        </button>
      </div>
    )
  }

  return (
    <div className="min-h-[calc(100vh-64px)] bg-[#f9fafb] p-6">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex items-center justify-between border-b border-gray-200 pb-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Your Projects</h1>
            <p className="mt-1 text-sm text-gray-500">
              {loading ? 'Loading…' : `${total} project${total === 1 ? '' : 's'}${filter ? ` · filtered by ${statusMeta(filter).label.toLowerCase()}` : ''}`}
            </p>
          </div>
          <button
            onClick={() => navigate('/new')}
            className="cursor-pointer rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            Start a New Project
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {FILTERS.map(([value, label]) => (
            <button
              key={value || 'all'}
              onClick={() => setFilter(value)}
              className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
                filter === value
                  ? 'border-blue-300 bg-blue-50 text-blue-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {label}
            </button>
          ))}
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            aria-label="Sort projects"
            className="ml-auto rounded border border-gray-200 bg-white px-2 py-1 text-xs font-semibold text-gray-600"
          >
            {SORTS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>

        {!loading && projects.length === 0 ? (
          <div className="mx-auto mt-12 flex max-w-xl flex-col items-center rounded-lg border border-gray-200 bg-white p-12 text-center shadow-sm">
            <div className="mb-4 text-5xl">{filter ? '🔍' : '🚀'}</div>
            <h3 className="mb-2 text-lg font-bold text-gray-900">
              {filter ? 'No projects with this status' : 'No projects yet'}
            </h3>
            <p className="mb-6 max-w-sm text-sm leading-relaxed text-gray-600">
              {filter
                ? 'Try a different filter, or start something new.'
                : 'Submit your idea and let the pipeline of autonomous agents build it for you.'}
            </p>
            <button
              onClick={() => (filter ? setFilter('') : navigate('/new'))}
              className="cursor-pointer rounded bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700"
            >
              {filter ? 'Clear filter' : 'Create Your First Project →'}
            </button>
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xs">
            <table className="w-full text-left">
              <thead className="border-b border-gray-200 bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Project</th>
                  <th className="px-4 py-2.5 font-semibold">Status</th>
                  <th className="px-4 py-2.5 font-semibold">Stage</th>
                  <th className="px-4 py-2.5 font-semibold">Output</th>
                  <th className="px-4 py-2.5 font-semibold">Created</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {projects.map((project) => {
                  const action = primaryAction(project)
                  return (
                    <tr key={project.id} className="hover:bg-gray-50/60">
                      <td className="px-4 py-3">
                        <Link to={`/projects/${project.id}`} className="text-sm font-semibold text-gray-900 hover:text-blue-600">
                          {project.name || 'Untitled'}
                        </Link>
                        <p className="mt-0.5 line-clamp-1 max-w-md text-xs text-gray-500">{project.brief}</p>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={project.status} interrupted={project.interrupted} />
                        {project.interrupted && (
                          <p className="mt-1 text-[10px] text-purple-600">Server stopped mid-run</p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-600">{stageLabel(project.current_stage)}</td>
                      <td className="px-4 py-3 text-xs text-gray-600">
                        {project.files_generated > 0 ? `${project.files_generated} files` : '—'}
                        {project.qa_issues_count > 0 && (
                          <span className="ml-1 text-amber-600">· {project.qa_issues_count} QA</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500">
                        {formatDate(project.created_at)}
                        {project.updated_at && (
                          <span className="block text-[10px] text-gray-400">{formatRelative(project.updated_at)}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1.5">
                          <Link
                            to={action.to}
                            className={`rounded px-2.5 py-1 text-xs font-semibold text-white ${action.className}`}
                          >
                            {action.label}
                          </Link>
                          <button
                            type="button"
                            onClick={() => setRestarting(project)}
                            disabled={project.status === 'running' && !project.interrupted}
                            title={project.status === 'running' && !project.interrupted
                              ? 'Cannot restart a project while it is running'
                              : 'Re-run from a chosen stage'}
                            className="rounded border border-gray-200 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-100 disabled:opacity-40"
                          >
                            Restart
                          </button>
                          <button
                            type="button"
                            onClick={() => setDeleting(project)}
                            className="rounded border border-gray-200 px-2.5 py-1 text-xs font-semibold text-red-600 hover:bg-red-50"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {restarting && (
        <RestartDialog
          projectId={restarting.id}
          projectName={restarting.name}
          onClose={() => setRestarting(null)}
          onDone={() => navigate(`/projects/${restarting.id}`)}
        />
      )}
      {deleting && (
        <DeleteDialog
          projectId={deleting.id}
          projectName={deleting.name}
          status={deleting.status}
          onClose={() => setDeleting(null)}
          onDone={() => {
            setProjects((rows) => rows.filter((r) => r.id !== deleting.id))
            setTotal((n) => Math.max(0, n - 1))
          }}
        />
      )}
    </div>
  )
}
