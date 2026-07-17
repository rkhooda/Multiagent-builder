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
  red: 'bg-red-100 text-red-700 border-red-200',
  orange: 'bg-orange-100 text-orange-700 border-orange-200',
  blue: 'bg-blue-100 text-blue-700 border-blue-200',
}

const CARD_STYLES = {
  red: 'border-red-200 bg-red-50',
  orange: 'border-orange-200 bg-orange-50',
  blue: 'border-blue-200 bg-blue-50',
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
      <div className="rounded-lg border border-gray-200 bg-white p-6 text-center text-sm text-gray-500">
        No QA report available for this project.
      </div>
    )
  }

  if (!hasParsedSections) {
    // Structure not recognized at all — render the whole report as markdown.
    return (
      <div className="max-h-[34rem] overflow-auto rounded-lg border border-gray-200 bg-white p-4">
        <div className="prose prose-sm max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{qaReport}</ReactMarkdown>
        </div>
      </div>
    )
  }

  return (
    <div className="max-h-[34rem] space-y-4 overflow-auto rounded-lg border border-gray-200 bg-white p-4">
      {/* Header badges */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-gray-300 bg-gray-100 px-2.5 py-1 text-xs font-bold text-gray-700">
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
            <h4 className="mb-2 text-sm font-bold text-gray-800">{s.key}</h4>
            {section.isEmpty || (section.parsed && section.findings.length === 0) ? (
              <p className="text-xs text-gray-500">No issues identified.</p>
            ) : !section.parsed ? (
              // Findings didn't match the expected shape — show the raw section.
              <div className="prose prose-sm max-w-none rounded border border-gray-200 bg-gray-50 p-3">
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
                      className="font-mono text-xs font-semibold text-blue-700 underline-offset-2 hover:underline"
                    >
                      {finding.file}
                      {finding.line ? `:${finding.line}` : ''}
                    </button>
                    <p className="mt-1 text-xs leading-relaxed text-gray-700">{finding.description}</p>
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
