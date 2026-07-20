import { useState } from 'react'

export default function FeedbackInput({ onSubmit, onCancel, submitting, label = 'What changes do you need?' }) {
  const [text, setText] = useState('')

  const handleSubmit = () => {
    if (!text.trim()) return
    onSubmit(text)
  }

  return (
    <div className="space-y-3 pt-3 border-t border-line">
      <label className="block text-xs font-semibold text-ink-2">
        {label}
      </label>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={label}
        rows={4}
        autoFocus
        className="w-full px-3 py-2 border border-line-strong rounded text-sm text-ink focus:ring-1 focus:ring-blue-500 focus:outline-none placeholder-gray-400"
      />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitting || !text.trim()}
          className={`flex-1 py-2 px-4 rounded text-ink font-semibold text-sm transition-colors ${
            submitting || !text.trim()
              ? 'bg-blue-300 cursor-not-allowed'
              : 'bg-overlay hover:bg-line cursor-pointer'
          }`}
        >
          {submitting ? 'Submitting...' : 'Submit'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="py-2 px-4 rounded text-sm font-semibold text-ink-2 border border-line-strong bg-raised hover:bg-overlay cursor-pointer"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
