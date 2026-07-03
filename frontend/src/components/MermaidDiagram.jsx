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
      <div className="rounded-lg border border-red-200 bg-red-50 p-3">
        <p className="text-sm font-medium text-red-600">Diagram could not be rendered</p>
        <p className="mt-1 text-xs text-red-500">{error}</p>
        <pre className="mt-2 overflow-x-auto text-xs text-gray-500">{code}</pre>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
      {title && (
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-gray-500">
          {title}
        </p>
      )}
      {isRendering && (
        <div className="flex items-center gap-2 py-4 text-sm text-gray-400">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-500" />
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
