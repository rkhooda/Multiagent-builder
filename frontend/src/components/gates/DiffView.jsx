import { diffLines } from 'diff'

export default function DiffView({ oldText, newText }) {
  const parts = diffLines(oldText || '', newText || '')

  return (
    <div className="max-h-[32rem] overflow-auto rounded border border-gray-200 bg-white p-3 font-mono text-xs leading-relaxed">
      {parts.map((part, index) => {
        const bg = part.added
          ? 'bg-green-50 text-green-800'
          : part.removed
            ? 'bg-red-50 text-red-800 line-through decoration-red-400'
            : 'text-gray-600'
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
