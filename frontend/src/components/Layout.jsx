import React, { useState, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { statusMeta } from '../lib/status'
import { applyTheme, storedTheme } from '../lib/theme'
import { Button, Dot, Eyebrow, cx } from './ui'

const API = ''

const TITLES = { '/': 'Projects', '/new': 'New project' }

function ThemeToggle() {
  const [theme, setTheme] = useState(storedTheme)
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button
      onClick={() => setTheme(applyTheme(next))}
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
      className="rounded-md border border-line-strong bg-overlay p-1.5 text-ink-2 transition-colors hover:border-ink-3 hover:text-ink"
    >
      {theme === 'dark' ? (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
        </svg>
      )}
    </button>
  )
}

export default function Layout({ children }) {
  const location = useLocation()
  const navigate = useNavigate()

  const [projects, setProjects] = useState([])
  const [health, setHealth] = useState('checking')   // checking | up | down
  const [navOpen, setNavOpen] = useState(false)

  const title =
    TITLES[location.pathname] ||
    (location.pathname.startsWith('/projects/') ? 'Project' : 'Agent Builder')

  useEffect(() => {
    let cancelled = false

    const check = async () => {
      try {
        const res = await fetch(`${API}/health`)
        if (!cancelled) setHealth(res.ok ? 'up' : 'down')
      } catch {
        if (!cancelled) setHealth('down')
      }
    }
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/projects`)
        if (!res.ok) return
        const data = await res.json()
        if (!cancelled) setProjects(data.projects || [])
      } catch {
        /* the sidebar list is not worth surfacing an error for */
      }
    }

    check()
    load()
    const interval = setInterval(() => { check(); load() }, 10000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  // Close the mobile drawer on navigation — otherwise it stays open over the
  // page you just asked for.
  useEffect(() => { setNavOpen(false) }, [location.pathname])

  // One definition, rendered in both the desktop rail and the mobile drawer, so
  // the navigation cannot drift between widths.
  const sidebar = (
    <>
      <div className="border-b border-line px-5 py-4">
        <Link to="/" className="group flex items-center gap-2.5">
          <span className="grid h-7 w-7 place-items-center rounded bg-accent-soft font-mono text-[13px] font-bold text-accent">
            A
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-ink transition-colors group-hover:text-accent">
            Agent Builder
          </span>
        </Link>
      </div>

      <div className="px-4 py-4">
        <Button variant="accent" className="w-full" onClick={() => navigate('/new')}>
          New project
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        <Eyebrow className="px-1 pb-2">Recent</Eyebrow>
        {projects.length === 0 ? (
          <p className="px-1 text-[13px] text-ink-3">No projects yet.</p>
        ) : (
          <ul className="space-y-0.5">
            {projects.map((project) => {
              const active = location.pathname === `/projects/${project.id}`
              const meta = statusMeta(project.status)
              return (
                <li key={project.id}>
                  <Link
                    to={`/projects/${project.id}`}
                    className={cx(
                      'flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-[13px] transition-colors',
                      active
                        ? 'bg-overlay font-medium text-ink'
                        : 'text-ink-2 hover:bg-overlay hover:text-ink',
                    )}
                  >
                    <span className="truncate">{project.name || 'Untitled'}</span>
                    <Dot tone={meta.tone} title={meta.label} />
                  </Link>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </>
  )

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-surface">
      <aside className="hidden w-[248px] shrink-0 flex-col border-r border-line bg-raised lg:flex">
        {sidebar}
      </aside>

      {navOpen && (
        <>
          <button
            aria-label="Close navigation"
            onClick={() => setNavOpen(false)}
            className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          />
          <aside className="fixed inset-y-0 left-0 z-50 flex w-[248px] flex-col border-r border-line bg-raised lg:hidden">
            {sidebar}
          </aside>
        </>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line bg-raised px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button
              onClick={() => setNavOpen(true)}
              aria-label="Open navigation"
              className="-ml-1 rounded-md p-1.5 text-ink-2 hover:bg-overlay hover:text-ink lg:hidden"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M3 6h18M3 12h18M3 18h18" />
              </svg>
            </button>
            <h1 className="truncate text-[15px] font-semibold tracking-tight text-ink">{title}</h1>
          </div>

          <div className="flex items-center gap-3">
            <span
              className="hidden items-center gap-1.5 sm:inline-flex"
              title={health === 'up' ? 'Backend reachable' : 'Backend unreachable'}
            >
              <Dot tone={health === 'up' ? 'ok' : health === 'down' ? 'err' : 'idle'} />
              <span className="eyebrow">
                {health === 'checking' ? 'Connecting' : health === 'up' ? 'Connected' : 'Offline'}
              </span>
            </span>
            <ThemeToggle />
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
