import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const SEVERITIES = [
  { key: 'Critical', color: 'red' },
  { key: 'Warnings', color: 'orange' },
  { key: 'Info', color: 'blue' },
]

const FINDING_RE = /-\s+\*\*File\*\*:\s*`([^`]+)`\s*\n\s*-\s+\*Issue\*:\s*(.+)/g

/**
 * Tolerant parser for the Day 12 QA report markdown.
 * Returns { sections: { Critical: {findings, raw, parsed}, ... }, issueCountByFile: Map }.
 * A section whose findings can't be parsed keeps parsed=false and its raw
 * markdown, so the panel can fall back to rendering it verbatim.
 */
export function parseQAReport(report) {
  const sections = {}
  const issueCountByFile = new Map()
  if (!report) return { sections, issueCountByFile }

  try {
    const headings = [...report.matchAll(/^##\s+(.+)$/gm)]
    headings.forEach((match, i) => {
      const title = match[1].trim()
      const start = match.index + match[0].length
      const end = i + 1 < headings.length ? headings[i + 1].index : report.length
      const body = report.slice(start, end).trim()

      if (!SEVERITIES.some((s) => s.key === title)) return

      const findings = []
      for (const m of body.matchAll(FINDING_RE)) {
        const [filePath, lineStr] = m[1].split(':')
        findings.push({ file: filePath, line: lineStr || null, description: m[2].trim() })
      }

      const isEmpty = /^no issues identified\.?$/i.test(body)
      const parsed = isEmpty || findings.length > 0 || body === ''
      sections[title] = { findings, raw: body, parsed, isEmpty }

      findings.forEach((f) => {
        issueCountByFile.set(f.file, (issueCountByFile.get(f.file) || 0) + 1)
      })
    })
  } catch {
    // Malformed report must never break gate 4 — callers fall back to raw markdown.
    return { sections: {}, issueCountByFile: new Map() }
  }

  return { sections, issueCountByFile }
}

const BADGE_STYLES = {
  red: 'bg-err/10 text-err border-err/35',
  orange: 'bg-accent-soft text-accent border-accent/35',
  blue: 'bg-run/10 text-run border-run/35',
}

const CARD_STYLES = {
  red: 'border-err/35 bg-err/10',
  orange: 'border-accent/35 bg-accent-soft',
  blue: 'border-run/35 bg-run/10',
}

export default function QAReportPanel({ qaReport, parsedReport, onOpenFile }) {
  const { sections } = parsedReport
  const hasParsedSections = Object.keys(sections).length > 0
  const totalIssues = SEVERITIES.reduce(
    (sum, s) => sum + (sections[s.key]?.findings.length || 0),
    0
  )

  if (!qaReport) {
    return (
      <div className="rounded-lg border border-line bg-raised p-6 text-center text-sm text-ink-3">
        No QA report available for this project.
      </div>
    )
  }

  if (!hasParsedSections) {
    // Structure not recognized at all — render the whole report as markdown.
    return (
      <div className="max-h-[34rem] overflow-auto rounded-lg border border-line bg-raised p-4">
        <div className="markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{qaReport}</ReactMarkdown>
        </div>
      </div>
    )
  }

  return (
    <div className="max-h-[34rem] space-y-4 overflow-auto rounded-lg border border-line bg-raised p-4">
      {/* Header badges */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-line-strong bg-overlay px-2.5 py-1 text-xs font-bold text-ink">
          {totalIssues} issue{totalIssues === 1 ? '' : 's'} total
        </span>
        {SEVERITIES.map((s) => (
          <span
            key={s.key}
            className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${BADGE_STYLES[s.color]}`}
          >
            {s.key}: {sections[s.key]?.findings.length ?? 0}
          </span>
        ))}
      </div>

      {SEVERITIES.map((s) => {
        const section = sections[s.key]
        if (!section) return null
        return (
          <div key={s.key}>
            <h4 className="mb-2 text-sm font-bold text-ink">{s.key}</h4>
            {section.isEmpty || (section.parsed && section.findings.length === 0) ? (
              <p className="text-xs text-ink-3">No issues identified.</p>
            ) : !section.parsed ? (
              // Findings didn't match the expected shape — show the raw section.
              <div className="prose prose-sm max-w-none rounded border border-line bg-overlay p-3">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{section.raw}</ReactMarkdown>
              </div>
            ) : (
              <div className="space-y-2">
                {section.findings.map((finding, i) => (
                  <div key={i} className={`rounded border p-3 ${CARD_STYLES[s.color]}`}>
                    <button
                      type="button"
                      onClick={() => onOpenFile(finding.file)}
                      title="Open this file in the browser"
                      className="font-mono text-xs font-semibold text-run underline-offset-2 hover:underline"
                    >
                      {finding.file}
                      {finding.line ? `:${finding.line}` : ''}
                    </button>
                    <p className="mt-1 text-xs leading-relaxed text-ink">{finding.description}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
