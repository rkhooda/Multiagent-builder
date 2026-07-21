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

// Skeletons, not examples to submit as-is — every one is written to be edited,
// and each carries the five parts that actually drive output quality: what it
// is, who it is for, features capped at 8, constraints, and an explicit
// out-of-scope list. The last is the one users skip and the one that pays most:
// the planner over-decomposes, so anything not excluded is work the run pays
// tokens to generate, and free-tier quota is the binding constraint.
const BRIEF_TEMPLATES = [
  {
    key: 'saas',
    label: 'SaaS Web App',
    hint: 'Multi-user product with accounts and billing',
    brief: `A [what it does] web app for [who it serves].

Target users: [describe them and the problem they have today — how do they cope without this?]

Core features (keep to 8 or fewer):
1. Email/password sign-up and login
2. [feature]
3. [feature]
4. [feature]
5. [feature]

Constraints: React frontend, FastAPI backend, PostgreSQL. Single-server deployment. Expect hundreds of users, not millions. [Add any tech you must use or must avoid.]

Out of scope: real-time collaboration, notifications, mobile apps, third-party integrations, admin dashboard, analytics, teams/organisations. [Cut anything here you genuinely need — and add everything you don't.]`,
  },
  {
    key: 'internal',
    label: 'Internal Tool / Dashboard',
    hint: 'Data views and admin actions for a small team',
    brief: `An internal [what it does] dashboard for [which team].

Target users: [role] at a [size] company, who currently do this with [spreadsheets / a manual process] and lose time to [specific pain].

Core features (keep to 8 or fewer):
1. A list view of [entity] with filtering and sorting
2. A detail view for a single [entity]
3. Create and edit [entity]
4. [a summary or metrics view]
5. [an export or bulk action]

Constraints: React frontend, FastAPI backend, PostgreSQL. Internal network only — single shared login is fine, no per-user permissions. Expect under 50 users and modest data volume.

Out of scope: public sign-up, role-based access control, audit logging, real-time updates, mobile layout, email notifications, integrations with other internal systems.`,
  },
  {
    key: 'api',
    label: 'API Backend',
    hint: 'Backend service only, no user interface',
    brief: `A REST API backend for [what it manages]. No user interface — this service is consumed by [which client].

Target users: [the developers or systems calling it, and what they need from it].

Core endpoints/features (keep to 8 or fewer):
1. API-key authentication
2. CRUD for [primary resource]
3. CRUD for [secondary resource]
4. [a list endpoint with filtering and pagination]
5. [any non-CRUD operation this service must perform]

Constraints: FastAPI, PostgreSQL with SQLAlchemy, Pydantic schemas for every request and response. Docker deployment. [State expected request volume.]

Out of scope: any frontend, user-facing auth flows (OAuth, password reset), webhooks, background job processing, rate limiting, caching, an admin interface.`,
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

  const applyTemplate = (template) => {
    // Only guard against destroying real typing — re-picking a template after
    // an accidental click should not cost a confirm.
    const typed = projectBrief.trim()
    const fromTemplate = BRIEF_TEMPLATES.some((t) => t.brief === projectBrief)
    if (typed && !fromTemplate &&
        !window.confirm('Replace what you have written with this template?')) {
      return
    }
    setProjectBrief(template.brief)
    setError('')
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
      const res = await fetch('/api/projects', {
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
            <div className="flex items-baseline justify-between mb-2 gap-3">
              <label className="block text-sm font-semibold text-ink">
                Project Brief <span className="text-err">*</span>
              </label>
              <span className="text-xs text-ink-3">
                Start from a template &mdash; then edit every bracket
              </span>
            </div>

            {/* Templates. The brief is the single biggest lever on output
                quality, so the structured starting point is offered before
                the empty box, not hidden behind help. */}
            <div className="flex flex-wrap gap-2 mb-3">
              {BRIEF_TEMPLATES.map((template) => (
                <button
                  key={template.key}
                  type="button"
                  onClick={() => applyTemplate(template)}
                  disabled={loading}
                  title={template.hint}
                  className="px-3 py-1.5 text-xs font-medium rounded border border-line-strong text-ink bg-overlay hover:bg-line hover:border-accent focus:ring-1 focus:ring-accent outline-none transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {template.label}
                </button>
              ))}
            </div>

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
            {/* Native <details> — no modal, no tooltip library, and it stays
                open while the user writes against it, which a tooltip cannot. */}
            <details className="mt-2 group">
              <summary className="text-xs text-ink-3 hover:text-ink cursor-pointer select-none marker:text-ink-3">
                Brief Best Practices &mdash; read this before your first run
              </summary>
              <div className="mt-3 bg-overlay border border-line rounded-lg p-4 text-xs text-ink-3 leading-relaxed space-y-3">
                <p className="text-ink font-medium">
                  The better the brief, the better the output. Nothing else you
                  control matters as much.
                </p>
                <p>
                  Every later stage &mdash; requirements, architecture, the task
                  plan, and every generated file &mdash; is derived from these
                  few sentences. A vague brief does not produce a vague but
                  workable project; it produces a plan too large to finish, and
                  the failure only becomes visible three stages later as missing
                  files.
                </p>

                <div>
                  <p className="text-ink font-medium mb-1">A good brief has five parts</p>
                  <ul className="list-disc list-inside space-y-0.5">
                    <li><span className="text-ink">What it is</span> &mdash; one or two sentences</li>
                    <li><span className="text-ink">Target users</span> &mdash; and what they do today instead</li>
                    <li><span className="text-ink">Core features</span> &mdash; eight at most</li>
                    <li><span className="text-ink">Constraints</span> &mdash; stack, scale, auth, deployment</li>
                    <li><span className="text-ink">Out of scope</span> &mdash; what NOT to build</li>
                  </ul>
                  <p className="mt-1.5">
                    That last one is skipped most often and pays most. The
                    planner over-decomposes &mdash; a todo app produced a
                    96-task, 95-file plan &mdash; so anything you do not exclude
                    is work this run will spend quota generating.
                  </p>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="border border-err/40 rounded p-3">
                    <p className="text-err font-medium mb-1">Bad</p>
                    <p className="italic">&ldquo;A social app for sharing recipes.&rdquo;</p>
                    <p className="mt-1.5">
                      One line constrains nothing. Users, scale, auth, feed,
                      comments, ratings, search &mdash; all invented, and the
                      plan balloons past what free quota can generate.
                    </p>
                  </div>
                  <div className="border border-ok/40 rounded p-3">
                    <p className="text-ok font-medium mb-1">Good</p>
                    <p className="italic">
                      5&ndash;10 sentences: a recipe app for home cooks; email
                      auth; create recipes with ingredients, steps and one
                      photo; public/private; browse and search by tag; save
                      others&rsquo; recipes. React + FastAPI + Postgres, single
                      server, hundreds of users.
                    </p>
                    <p className="mt-1.5">
                      Then: <span className="text-ink">out of scope</span> &mdash;
                      comments, ratings, following, notifications, meal
                      planning, shopping lists, mobile apps, social feed.
                    </p>
                  </div>
                </div>

                <p className="border-t border-line pt-3">
                  <span className="text-ink font-medium">On ambition.</span>{' '}
                  Quality degrades with complexity, and not gently. Real-time
                  collaboration, heavily stateful designs and anything needing
                  live coordination between users sit at or past this
                  system&rsquo;s ceiling &mdash; the run keeps going rather than
                  failing outright, but expect a thin, skeletal result. On free
                  tiers, conventional CRUD applications are where this works
                  best. Aim there first.
                </p>
              </div>
            </details>
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
