import React, { useState } from 'react'

export default function ApprovalGate({ status, gateEvent, currentStage, eventsCount, onResume }) {
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [feedbackText, setFeedbackText] = useState('')
  const [submitting, setSubmitting] = useState(null) // 'approve' | 'edit' | null

  const gateName = gateEvent?.gate || ''
  
  // Derive gate title and description
  const getGateInfo = (name) => {
    switch (name) {
      case 'human_gate_1':
        return {
          title: 'Gate 1 — Research & Requirements Review',
          desc: 'The agents have completed the research phase and gathered initial requirements. Please review the findings to proceed.'
        }
      case 'human_gate_2':
        return {
          title: 'Gate 2 — Architecture Review',
          desc: 'The tech stack and database schema have been determined. Review the system architecture blueprint.'
        }
      case 'human_gate_3':
        return {
          title: 'Gate 3 — Design & Plan Review',
          desc: 'The step-by-step implementation plan is ready. Verify the codebase outline and file design.'
        }
      case 'human_gate_4':
        return {
          title: 'Gate 4 — Final Deployment Review',
          desc: 'The code files have been written, database integrated, QA tests run, and DevOps configured. Perform the final check.'
        }
      default:
        // Try parsing number
        const num = name.replace(/[^0-9]/g, '')
        return {
          title: `Gate ${num || 'Review'} — Custom Stage Review`,
          desc: `Please review the artifacts generated for ${name} before proceeding.`
        }
    }
  }

  const handleApprove = async () => {
    setSubmitting('approve')
    try {
      await onResume('approve', '')
      setFeedbackOpen(false)
      setFeedbackText('')
    } catch (err) {
      console.error(err)
    } finally {
      setSubmitting(null)
    }
  }

  const handleSubmitChanges = async () => {
    if (!feedbackText.trim()) return
    setSubmitting('edit')
    try {
      await onResume('edit', feedbackText)
      setFeedbackOpen(false)
      setFeedbackText('')
    } catch (err) {
      console.error(err)
    } finally {
      setSubmitting(null)
    }
  }

  // Render based on state
  if (status === 'done') {
    return (
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6 text-center space-y-4">
        <div className="text-4xl">🎉</div>
        <h3 className="text-xl font-bold text-gray-900">Project Complete!</h3>
        <p className="text-sm text-gray-600">
          All agents have finished executing the pipeline. Your project is ready.
        </p>
        <div className="pt-2">
          <button
            disabled
            className="w-full bg-gray-100 text-gray-400 font-medium py-2.5 px-4 rounded border border-gray-200 cursor-not-allowed text-sm"
          >
            Download Project (Placeholder)
          </button>
        </div>
      </div>
    )
  }

  if (status === 'awaiting_approval' && gateName) {
    const { title, desc } = getGateInfo(gateName)
    const isPending = submitting !== null

    return (
      <div className="bg-white rounded-lg border border-orange-200 shadow-sm p-6 space-y-4">
        <div className="flex items-center space-x-2">
          <span className="h-2.5 w-2.5 rounded-full bg-orange-500 animate-pulse" />
          <h3 className="text-base font-bold text-gray-900 uppercase tracking-wide">
            Human Gate Active
          </h3>
        </div>

        <h4 className="text-lg font-bold text-gray-800">{title}</h4>
        <p className="text-sm text-gray-600 leading-relaxed">{desc}</p>

        {submitting ? (
          <div className="text-sm text-blue-600 font-medium italic animate-pulse">
            Pipeline resuming...
          </div>
        ) : (
          <div className="space-y-4 pt-2">
            <div className="flex space-x-3">
              <button
                onClick={handleApprove}
                disabled={isPending}
                className="flex-1 bg-green-600 hover:bg-green-700 text-white font-semibold py-2 px-3 rounded text-sm transition-colors cursor-pointer text-center"
              >
                {submitting === 'approve' ? 'Sending...' : '✅ Approve & Continue'}
              </button>
              <button
                onClick={() => setFeedbackOpen(!feedbackOpen)}
                disabled={isPending}
                className="flex-1 bg-white hover:bg-gray-50 text-gray-700 border border-gray-300 font-semibold py-2 px-3 rounded text-sm transition-colors cursor-pointer text-center"
              >
                ✏️ Request Changes
              </button>
            </div>

            {feedbackOpen && (
              <div className="space-y-3 pt-2 border-t border-gray-100">
                <label className="block text-xs font-semibold text-gray-600">
                  Feedback / Request details:
                </label>
                <textarea
                  value={feedbackText}
                  onChange={(e) => setFeedbackText(e.target.value)}
                  placeholder="Tell the agents what to change or refine..."
                  rows={4}
                  className="w-full px-3 py-2 border border-gray-300 rounded text-sm text-gray-900 focus:ring-1 focus:ring-blue-500 focus:outline-none placeholder-gray-400"
                />
                <button
                  onClick={handleSubmitChanges}
                  disabled={isPending || !feedbackText.trim()}
                  className={`w-full py-2 px-4 rounded text-white font-semibold text-sm transition-colors cursor-pointer text-center ${
                    !feedbackText.trim()
                      ? 'bg-blue-300 cursor-not-allowed'
                      : 'bg-blue-600 hover:bg-blue-700'
                  }`}
                >
                  {submitting === 'edit' ? 'Sending...' : 'Submit Changes'}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  // Default: Pipeline running state
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6 space-y-4">
      <div className="flex items-center space-x-2">
        <span className="h-2.5 w-2.5 rounded-full bg-blue-500 animate-pulse" />
        <h3 className="text-sm font-semibold text-gray-700">Agents working...</h3>
      </div>

      <div className="space-y-2">
        <div className="text-xs text-gray-500">Current Stage:</div>
        <div className="text-base font-bold text-gray-900 capitalize">
          {currentStage || 'Initializing...'}
        </div>
      </div>

      <div className="border-t border-gray-100 pt-3 flex justify-between items-center text-xs text-gray-500">
        <span>Events Streamed:</span>
        <span className="font-semibold text-gray-700">{eventsCount}</span>
      </div>
    </div>
  )
}
