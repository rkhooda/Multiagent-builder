import { useEffect, useRef, useState } from 'react'
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter'
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python'
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript'
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json'
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml'
import markdown from 'react-syntax-highlighter/dist/esm/languages/prism/markdown'
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash'
import docker from 'react-syntax-highlighter/dist/esm/languages/prism/docker'
import { oneLight, oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'

SyntaxHighlighter.registerLanguage('python', python)
SyntaxHighlighter.registerLanguage('javascript', javascript)
SyntaxHighlighter.registerLanguage('jsx', jsx)
SyntaxHighlighter.registerLanguage('sql', sql)
SyntaxHighlighter.registerLanguage('json', json)
SyntaxHighlighter.registerLanguage('yaml', yaml)
SyntaxHighlighter.registerLanguage('markdown', markdown)
SyntaxHighlighter.registerLanguage('bash', bash)
SyntaxHighlighter.registerLanguage('docker', docker)

// Backend language name -> registered Prism language
const PRISM_LANGUAGE = {
  python: 'python', javascript: 'javascript', jsx: 'jsx',
  typescript: 'javascript', tsx: 'jsx', sql: 'sql', json: 'json',
  yaml: 'yaml', markdown: 'markdown', bash: 'bash', docker: 'docker',
  nginx: 'bash', html: 'markdown', css: 'javascript',
}

const DOT_COLOR = {
  python: 'bg-run',
  javascript: 'bg-warn', jsx: 'bg-warn', typescript: 'bg-warn', tsx: 'bg-warn',
  sql: 'bg-accent',
  markdown: 'bg-idle',
  json: 'bg-ok', yaml: 'bg-ok',
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function buildTree(files) {
  const root = { name: '', path: '', children: new Map(), file: null }
  files.forEach((file) => {
    const parts = file.path.split('/')
    let node = root
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1
      const childPath = parts.slice(0, i + 1).join('/')
      if (!node.children.has(part)) {
        node.children.set(part, { name: part, path: childPath, children: new Map(), file: null })
      }
      node = node.children.get(part)
      if (isFile) node.file = file
    })
  })
  const toArray = (node) => {
    const children = [...node.children.values()].map(toArray)
    children.sort((a, b) => {
      if (!!a.file !== !!b.file) return a.file ? 1 : -1 // folders first
      return a.name.localeCompare(b.name)
    })
    return { ...node, children }
  }
  return toArray(root).children
}

function TreeNode({ node, depth, selectedPath, collapsed, onToggle, onSelect, issueCountByFile }) {
  const isFolder = !node.file
  const isCollapsed = collapsed.has(node.path)
  const indent = { paddingLeft: `${depth * 14 + 8}px` }

  if (isFolder) {
    return (
      <div>
        <button
          type="button"
          onClick={() => onToggle(node.path)}
          style={indent}
          className="flex w-full items-center gap-1.5 py-1 pr-2 text-left text-xs font-semibold text-ink hover:bg-overlay"
        >
          <span className="text-ink-3">{isCollapsed ? '▸' : '▾'}</span>
          <span className="truncate">{node.name}/</span>
        </button>
        {!isCollapsed &&
          node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              collapsed={collapsed}
              onToggle={onToggle}
              onSelect={onSelect}
              issueCountByFile={issueCountByFile}
            />
          ))}
      </div>
    )
  }

  const issueCount = issueCountByFile?.get(node.path) || 0
  const selected = selectedPath === node.path
  return (
    <button
      type="button"
      onClick={() => onSelect(node.path)}
      style={indent}
      className={`flex w-full items-center gap-1.5 py-1 pr-2 text-left text-xs hover:bg-overlay ${
        selected ? 'bg-run/10 font-semibold text-run' : 'text-ink'
      }`}
    >
      <span className={`h-2 w-2 flex-shrink-0 rounded-full ${DOT_COLOR[node.file.language] || 'bg-idle'}`} />
      <span className="truncate">{node.name}</span>
      {issueCount > 0 && (
        <span
          title={`${issueCount} QA issue${issueCount > 1 ? 's' : ''}`}
          className="ml-auto flex h-4 min-w-4 flex-shrink-0 items-center justify-center rounded-full bg-accent-soft px-1 text-[10px] font-bold text-accent"
        >
          {issueCount}
        </span>
      )}
      <span className={`flex-shrink-0 text-[10px] text-ink-3 ${issueCount > 0 ? '' : 'ml-auto'}`}>
        {formatBytes(node.file.size_bytes)}
      </span>
    </button>
  )
}

/** Follows the live theme, since the user can flip it while a file is open. */
function useIsDark() {
  const [dark, setDark] = useState(
    () => document.documentElement.getAttribute('data-theme') !== 'light',
  )
  useEffect(() => {
    const el = document.documentElement
    const observer = new MutationObserver(() =>
      setDark(el.getAttribute('data-theme') !== 'light'))
    observer.observe(el, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])
  return dark
}

function PreviewShimmer() {
  return (
    <div className="animate-pulse space-y-2 p-4">
      {[...Array(12)].map((_, i) => (
        <div key={i} className="h-3 rounded bg-overlay" style={{ width: `${45 + ((i * 37) % 50)}%` }} />
      ))}
    </div>
  )
}

export default function FileBrowser({
  projectId,
  filesData,
  issueCountByFile,
  selectedPath,
  onSelectPath,
  onRequestFix,
  fixDisabledReason,
  contentVersion, // bump to invalidate the cache for a path after an AI fix
  previousContent, // path -> pre-fix content, enables the View Changes toggle
  renderDiff,
}) {
  const isDark = useIsDark()
  const [collapsed, setCollapsed] = useState(new Set())
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [showDiff, setShowDiff] = useState(false)
  const cacheRef = useRef(new Map())

  const files = filesData?.files || []
  const tree = buildTree(files)
  const selectedFile = files.find((f) => f.path === selectedPath) || null

  useEffect(() => {
    if (!selectedPath) return
    setShowDiff(false)
    const cacheKey = `${selectedPath}@${contentVersion?.[selectedPath] || 0}`
    if (cacheRef.current.has(cacheKey)) {
      setContent(cacheRef.current.get(cacheKey))
      setLoadError('')
      return
    }
    let cancelled = false
    setLoading(true)
    setLoadError('')
    fetch(
      `/api/projects/${projectId}/files/content?path=${encodeURIComponent(selectedPath)}`
    )
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.json())?.detail || `HTTP ${res.status}`)
        return res.text()
      })
      .then((text) => {
        if (cancelled) return
        cacheRef.current.set(cacheKey, text)
        setContent(text)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(String(err.message || err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId, selectedPath, contentVersion])

  const handleToggle = (path) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const oldContent = selectedPath ? previousContent?.[selectedPath] : null

  return (
    <div className="flex min-h-0 rounded-lg border border-line bg-raised" style={{ height: '34rem' }}>
      {/* Tree */}
      <div className="w-[30%] flex-shrink-0 overflow-y-auto border-r border-line py-2">
        {tree.length === 0 ? (
          <p className="px-3 py-2 text-xs text-ink-3">No generated files found.</p>
        ) : (
          tree.map((node) => (
            <TreeNode
              key={node.path}
              node={node}
              depth={0}
              selectedPath={selectedPath}
              collapsed={collapsed}
              onToggle={handleToggle}
              onSelect={onSelectPath}
              issueCountByFile={issueCountByFile}
            />
          ))
        )}
      </div>

      {/* Preview */}
      <div className="flex min-w-0 flex-1 flex-col">
        {!selectedFile ? (
          <div className="flex flex-1 items-center justify-center text-sm text-ink-3">
            Select a file to preview it
          </div>
        ) : (
          <>
            <div className="flex flex-shrink-0 items-center gap-2 border-b border-line bg-overlay px-3 py-2">
              <span className="truncate font-mono text-xs font-semibold text-ink">{selectedFile.path}</span>
              <span className="rounded bg-overlay px-1.5 py-0.5 text-[10px] font-semibold uppercase text-ink-2">
                {selectedFile.language}
              </span>
              <span className="flex-shrink-0 text-[10px] text-ink-3">
                {formatBytes(selectedFile.size_bytes)} · {selectedFile.line_count ?? '?'} lines
              </span>
              <div className="ml-auto flex flex-shrink-0 items-center gap-2">
                {oldContent != null && (
                  <button
                    type="button"
                    onClick={() => setShowDiff((v) => !v)}
                    className="rounded border border-line-strong bg-raised px-2 py-1 text-[11px] font-semibold text-ink hover:bg-overlay"
                  >
                    {showDiff ? 'Hide Changes' : 'View Changes'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onRequestFix(selectedFile.path)}
                  disabled={Boolean(fixDisabledReason)}
                  title={fixDisabledReason || 'Ask an AI agent to fix this file'}
                  className="rounded bg-overlay px-2.5 py-1 text-[11px] font-semibold text-ink hover:bg-line disabled:cursor-not-allowed disabled:opacity-50"
                >
                  🛠 Request AI Fix
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              {loading ? (
                <PreviewShimmer />
              ) : loadError ? (
                <p className="p-4 text-xs text-err">Failed to load file: {loadError}</p>
              ) : showDiff && oldContent != null ? (
                <div className="p-2">{renderDiff(oldContent, content)}</div>
              ) : (
                <SyntaxHighlighter
                  language={PRISM_LANGUAGE[selectedFile.language] || 'text'}
                  style={isDark ? oneDark : oneLight}
                  showLineNumbers
                  customStyle={{
                    margin: 0,
                    fontSize: '12px',
                    background: 'transparent',
                    fontFamily: 'var(--font-mono)',
                  }}
                  lineNumberStyle={{ color: 'var(--ink-3)', minWidth: '2.5em' }}
                  // The prism theme paints its own slab background on the code
                  // element too; without this it shows through as grey blocks.
                  codeTagProps={{ style: { background: 'transparent' } }}
                >
                  {content}
                </SyntaxHighlighter>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
