import { useState, useEffect } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const AGENT_TYPES = [
  { value: 'research', label: 'Research (Gemini 2.5 Flash / Groq Llama 3.3)' },
  { value: 'requirements', label: 'Requirements (Gemini 2.5 Flash / DeepSeek V3)' },
  { value: 'architecture', label: 'Architecture (DeepSeek V3 / Gemini 2.5 Flash)' },
  { value: 'planning', label: 'Planning (Gemini 2.5 Flash / Groq Llama 3.3)' },
  { value: 'frontend_code', label: 'Frontend Code (Qwen Coder / DeepSeek V3)' },
  { value: 'backend_code', label: 'Backend Code (DeepSeek V3 / Qwen Coder)' },
  { value: 'database', label: 'Database (Groq Llama 3.3 / DeepSeek V3)' },
  { value: 'qa', label: 'QA (DeepSeek R1 / Gemini 2.5 Flash)' },
  { value: 'devops', label: 'Devops (Groq Llama 3.3 / Gemini 2.5 Flash)' }
]

function App() {
  // Health states
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // LLM Test states
  const [agentType, setAgentType] = useState('research')
  const [prompt, setPrompt] = useState('Say hello in exactly 10 words')
  const [testResult, setTestResult] = useState('')
  const [testLoading, setTestLoading] = useState(false)
  const [testError, setTestError] = useState(null)

  useEffect(() => {
    axios.get('http://localhost:8000/health')
      .then(response => {
        setHealth(response.data)
        setLoading(false)
      })
      .catch(err => {
        console.error("Error fetching health status:", err)
        setError(err.message || 'Failed to connect to backend')
        setLoading(false)
      })
  }, [])

  const handleTestLLM = (e) => {
    e.preventDefault()
    setTestLoading(true)
    setTestError(null)
    setTestResult('')

    axios.post('http://localhost:8000/api/test-llm', {
      prompt: prompt,
      agent_type: agentType
    })
      .then(response => {
        setTestResult(response.data.response)
        setTestLoading(false)
      })
      .catch(err => {
        console.error("LLM Test Error:", err)
        const errorMsg = err.response?.data?.detail || err.message || 'Failed to call LLM'
        setTestError(errorMsg)
        setTestLoading(false)
      })
  }

  return (
    <div className="min-h-screen bg-[#0d0e12] text-[#f3f4f6] flex flex-col items-center justify-center p-6 relative overflow-hidden font-sans">
      {/* Background Gradients */}
      <div className="absolute top-[-20%] left-[-10%] w-[600px] h-[600px] rounded-full bg-purple-900/10 blur-[130px] pointer-events-none" />
      <div className="absolute bottom-[-20%] right-[-10%] w-[600px] h-[600px] rounded-full bg-blue-900/10 blur-[130px] pointer-events-none" />

      <div className="w-full max-w-2xl space-y-6 z-10">
        
        {/* Main Glassmorphic Card (Status Gateway) */}
        <div className="bg-[#16171d]/85 backdrop-blur-xl border border-white/10 rounded-2xl p-6 shadow-2xl transition-all duration-300 hover:border-purple-500/20">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-white/5 pb-4 mb-4">
            <div className="flex items-center space-x-3">
              <div className="p-2 bg-purple-500/10 border border-purple-500/20 rounded-lg">
                <svg className="w-6 h-6 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path>
                </svg>
              </div>
              <div>
                <h1 className="text-xl font-bold tracking-tight bg-gradient-to-r from-purple-400 via-indigo-400 to-blue-400 bg-clip-text text-transparent">
                  Multi-Agent AI Builder
                </h1>
                <p className="text-xs text-gray-400">Gateway Console</p>
              </div>
            </div>

            {/* Status Info */}
            <div>
              {loading ? (
                <div className="flex items-center space-x-2 text-purple-400 text-xs">
                  <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  <span>Checking...</span>
                </div>
              ) : error ? (
                <span className="px-2.5 py-1 bg-rose-500/10 border border-rose-500/25 text-rose-400 rounded-full text-xs font-medium">
                  Offline
                </span>
              ) : (
                <div className="flex items-center space-x-2">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                  </span>
                  <span className="px-2.5 py-1 bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 rounded-full text-xs font-semibold">
                    Online: v{health?.version}
                  </span>
                </div>
              )}
            </div>
          </div>

          {error && (
            <div className="p-3.5 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-xs font-mono">
              <span className="font-bold">Gateway Connection Failure:</span> {error}
            </div>
          )}
        </div>

        {/* LLM Playground Card */}
        <div className="bg-[#16171d]/85 backdrop-blur-xl border border-white/10 rounded-2xl p-6 shadow-2xl transition-all duration-300 hover:border-purple-500/20">
          <div className="flex items-center space-x-2 mb-4 border-b border-white/5 pb-3">
            <svg className="w-5 h-5 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
            </svg>
            <h2 className="text-base font-bold text-gray-200">LLM Router Playground</h2>
          </div>

          <form onSubmit={handleTestLLM} className="space-y-4">
            {/* Agent Selector */}
            <div>
              <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Agent Type (Assigned LLMs)
              </label>
              <select
                value={agentType}
                onChange={(e) => setAgentType(e.target.value)}
                className="w-full bg-[#1f2028] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-gray-200 focus:outline-none focus:border-purple-500 transition-colors"
              >
                {AGENT_TYPES.map((agent) => (
                  <option key={agent.value} value={agent.value}>
                    {agent.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Prompt Input */}
            <div>
              <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Prompt
              </label>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={2}
                className="w-full bg-[#1f2028] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-gray-200 focus:outline-none focus:border-purple-500 focus:ring-1 focus:ring-purple-500/20 transition-all resize-none"
                placeholder="Enter prompt to send to the router..."
              />
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={testLoading}
              className="w-full bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 disabled:opacity-50 text-white text-sm font-semibold py-2.5 px-4 rounded-xl shadow-lg transition-all duration-200 flex items-center justify-center space-x-2"
            >
              {testLoading ? (
                <>
                  <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  <span>Querying Router...</span>
                </>
              ) : (
                <span>Test LLM Router</span>
              )}
            </button>
          </form>

          {/* Results section */}
          {(testResult || testError) && (
            <div className="mt-5 space-y-2 animate-fadeIn">
              <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wider">
                Output
              </label>
              
              {testError && (
                <div className="p-4 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-xs font-mono">
                  <span className="font-bold">Error:</span> {testError}
                </div>
              )}

              {testResult && (
                <div className="p-4 bg-[#1f2028] border border-white/5 text-gray-200 rounded-xl text-sm font-mono whitespace-pre-wrap leading-relaxed shadow-inner">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{testResult}</ReactMarkdown>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-between items-center text-xs text-gray-600 px-2">
          <span>Day 2 Setup Complete</span>
          <span>Agent Routing Interface</span>
        </div>

      </div>
    </div>
  )
}

export default App
