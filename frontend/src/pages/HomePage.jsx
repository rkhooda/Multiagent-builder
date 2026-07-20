import React, { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { RestartDialog, DeleteDialog } from '../components/LifecycleDialogs'
import { statusMeta, stageLabel, formatDate, formatRelative } from '../lib/status'
import { Badge, Button, Card, Dot, Skeleton, cx } from '../components/ui'

const API = '/api/projects'

// Filter options mirror the canonical backend vocabulary; `interrupted` is
// derived per row rather than stored, so it is not a server-side filter.
const FILTERS = [
  ['', 'All'],
  ['awaiting_approval', 'Needs you'],
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
    <Badge tone={meta.tone}>
      <Dot tone={meta.tone} />
      {meta.label}
    </Badge>
  )
}

/**
 * Status-appropriate row action — what the user most likely wants next for a
 * project in that state, so the list is operable without opening each project.
 *
 * Only a gate waiting on a decision gets the filled accent button. Everything
 * else is secondary, which is what makes a row that needs you findable in a
 * long list at a glance.
 */
function primaryAction(project) {
  if (project.interrupted) return { label: 'Resume', variant: 'secondary' }
  switch (project.status) {
    case 'awaiting_approval': return { label: 'Review', variant: 'primary' }
    case 'error_paused':
    case 'rate_limited': return { label: 'Retry', variant: 'secondary' }
    case 'running': return { label: 'Watch', variant: 'secondary' }
    default: return { label: 'Open', variant: 'secondary' }
  }
}

function SkeletonRows() {
  return Array.from({ length: 5 }, (_, i) => (
    <tr key={i} className="border-t border-line">
      <td className="px-4 py-3">
        <Skeleton className="h-3.5 w-40" />
        <Skeleton className="mt-2 h-2.5 w-64" />
      </td>
      <td className="px-4 py-3"><Skeleton className="h-4 w-20" /></td>
      <td className="hidden px-4 py-3 md:table-cell"><Skeleton className="h-3 w-24" /></td>
      <td className="hidden px-4 py-3 lg:table-cell"><Skeleton className="h-3 w-16" /></td>
      <td className="hidden px-4 py-3 lg:table-cell"><Skeleton className="h-3 w-20" /></td>
      <td className="px-4 py-3"><Skeleton className="ml-auto h-7 w-28" /></td>
    </tr>
  ))
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
      setError('Could not reach the backend. Check that it is running on port 8000.')
    } finally {
      setLoading(false)
    }
  }, [filter, sort])

  useEffect(() => { fetchProjects() }, [fetchProjects])

  if (error) {
    return (
      <div className="grid min-h-full place-items-center p-6">
        <Card className="max-w-md text-center">
          <Dot tone="err" className="mx-auto mb-3 h-2 w-2" />
          <h2 className="text-[15px] font-semibold text-ink">Backend unreachable</h2>
          <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">{error}</p>
          <Button variant="secondary" className="mt-4" onClick={fetchProjects}>
            Try again
          </Button>
        </Card>
      </div>
    )
  }

  const empty = !loading && projects.length === 0

  return (
    <div className="p-4 sm:p-6">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold tracking-tight text-ink">Projects</h2>
            <p className="mt-1 text-[13px] text-ink-2">
              {loading
                ? 'Loading…'
                : `${total} project${total === 1 ? '' : 's'}${
                    filter ? ` · ${statusMeta(filter).label.toLowerCase()}` : ''
                  }`}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {FILTERS.map(([value, label]) => (
            <button
              key={value || 'all'}
              onClick={() => setFilter(value)}
              className={cx(
                'rounded-md border px-2.5 py-1 font-mono text-[11px] font-semibold uppercase tracking-wider transition-colors',
                filter === value
                  ? 'border-line-strong bg-overlay text-ink'
                  : 'border-line bg-raised text-ink-3 hover:border-line-strong hover:text-ink-2',
              )}
            >
              {label}
            </button>
          ))}
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            aria-label="Sort projects"
            className="ml-auto rounded-md border border-line bg-raised px-2 py-1 text-[12px] text-ink-2"
          >
            {SORTS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>

        {empty ? (
          <Card className="mx-auto mt-10 max-w-lg py-12 text-center">
            <h3 className="text-[15px] font-semibold text-ink">
              {filter ? 'Nothing with this status' : 'No projects yet'}
            </h3>
            <p className="mx-auto mt-2 max-w-sm text-[13px] leading-relaxed text-ink-2">
              {filter
                ? 'Try a different filter, or start something new.'
                : 'Describe what you want built. The pipeline researches it, plans it, and generates the scaffold — pausing at four gates for your review.'}
            </p>
            <Button
              variant={filter ? 'secondary' : 'accent'}
              className="mt-5"
              onClick={() => (filter ? setFilter('') : navigate('/new'))}
            >
              {filter ? 'Clear filter' : 'Create your first project'}
            </Button>
          </Card>
        ) : (
          <Card pad={false} className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left">
                <thead>
                  <tr className="border-b border-line bg-overlay/60">
                    <th className="eyebrow px-4 py-2.5">Project</th>
                    <th className="eyebrow px-4 py-2.5">Status</th>
                    <th className="eyebrow hidden px-4 py-2.5 md:table-cell">Stage</th>
                    <th className="eyebrow hidden px-4 py-2.5 lg:table-cell">Output</th>
                    <th className="eyebrow hidden px-4 py-2.5 lg:table-cell">Created</th>
                    <th className="eyebrow px-4 py-2.5 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <SkeletonRows />
                  ) : (
                    projects.map((project) => {
                      const action = primaryAction(project)
                      return (
                        <tr
                          key={project.id}
                          className="border-t border-line transition-colors hover:bg-overlay/50"
                        >
                          <td className="px-4 py-3">
                            <Link
                              to={`/projects/${project.id}`}
                              className="text-[13px] font-semibold text-ink transition-colors hover:text-accent"
                            >
                              {project.name || 'Untitled'}
                            </Link>
                            <p className="mt-0.5 line-clamp-1 max-w-md text-[12px] text-ink-3">
                              {project.brief}
                            </p>
                          </td>
                          <td className="px-4 py-3">
                            <StatusBadge status={project.status} interrupted={project.interrupted} />
                            {project.interrupted && (
                              <p className="mt-1 text-[10px] text-alt">Server stopped mid-run</p>
                            )}
                          </td>
                          <td className="hidden px-4 py-3 text-[12px] text-ink-2 md:table-cell">
                            {stageLabel(project.current_stage)}
                          </td>
                          <td className="hidden px-4 py-3 text-[12px] text-ink-2 lg:table-cell">
                            {project.files_generated > 0 ? `${project.files_generated} files` : '—'}
                            {project.qa_issues_count > 0 && (
                              <span className="ml-1 text-warn">· {project.qa_issues_count} QA</span>
                            )}
                          </td>
                          <td className="hidden whitespace-nowrap px-4 py-3 text-[12px] text-ink-3 lg:table-cell">
                            {formatDate(project.created_at)}
                            {project.updated_at && (
                              <span className="block text-[10px] text-ink-3">
                                {formatRelative(project.updated_at)}
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center justify-end gap-1.5">
                              <Button
                                size="sm"
                                variant={action.variant}
                                onClick={() => navigate(`/projects/${project.id}`)}
                              >
                                {action.label}
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setRestarting(project)}
                                disabled={project.status === 'running' && !project.interrupted}
                                title={
                                  project.status === 'running' && !project.interrupted
                                    ? 'Cannot restart a project while it is running'
                                    : 'Re-run from a chosen stage'
                                }
                              >
                                Restart
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="text-err hover:bg-err/10 hover:text-err"
                                onClick={() => setDeleting(project)}
                              >
                                Delete
                              </Button>
                            </div>
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          </Card>
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
