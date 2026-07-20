import { diffLines } from 'diff'

export default function DiffView({ oldText, newText }) {
  const parts = diffLines(oldText || '', newText || '')

  return (
    <div className="max-h-[32rem] overflow-auto rounded border border-line bg-raised p-3 font-mono text-xs leading-relaxed">
      {parts.map((part, index) => {
        const bg = part.added
          ? 'bg-ok/10 text-ok'
          : part.removed
            ? 'bg-err/10 text-err line-through decoration-red-400'
            : 'text-ink-2'
        const prefix = part.added ? '+ ' : part.removed ? '- ' : '  '

        return (
          <pre key={index} className={`whitespace-pre-wrap break-words px-1 ${bg}`}>
            {part.value
              .split('\n')
              .filter((_, i, arr) => !(i === arr.length - 1 && arr[i] === ''))
              .map((line, lineIndex) => (
                <div key={lineIndex}>{prefix}{line}</div>
              ))}
          </pre>
        )
      })}
    </div>
  )
}
