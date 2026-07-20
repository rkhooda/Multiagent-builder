import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'

mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'loose',
  er: { diagramPadding: 20 },
  flowchart: { curve: 'linear' }
})

let diagramCounter = 0

export default function MermaidDiagram({ code, title }) {
  const containerRef = useRef(null)
  const [error, setError] = useState(null)
  const [isRendering, setIsRendering] = useState(true)

  useEffect(() => {
    if (!code || !containerRef.current) return undefined

    const id = `mermaid-diagram-${++diagramCounter}`
    let isActive = true

    setIsRendering(true)
    setError(null)
    containerRef.current.innerHTML = ''

    const renderDiagram = async () => {
      try {
        const { svg } = await mermaid.render(id, code.trim())
        if (isActive && containerRef.current) {
          containerRef.current.innerHTML = svg
          setIsRendering(false)
        }
      } catch (err) {
        console.error('[MermaidDiagram] Render error:', err)
        if (isActive) {
          setError(`Diagram render failed: ${err.message}`)
          setIsRendering(false)
        }
      }
    }

    renderDiagram()

    return () => {
      isActive = false
    }
  }, [code])

  if (error) {
    return (
      <div className="rounded-lg border border-err/35 bg-err/10 p-3">
        <p className="text-sm font-medium text-err">Diagram could not be rendered</p>
        <p className="mt-1 text-xs text-err">{error}</p>
        <pre className="mt-2 overflow-x-auto text-xs text-ink-3">{code}</pre>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-line bg-raised p-3 ">
      {title && (
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-ink-3">
          {title}
        </p>
      )}
      {isRendering && (
        <div className="flex items-center gap-2 py-4 text-sm text-ink-3">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-line-strong border-t-blue-500" />
          Rendering diagram...
        </div>
      )}
      <div
        ref={containerRef}
        className="overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full"
        style={{ display: isRendering ? 'none' : 'block' }}
      />
    </div>
  )
}
