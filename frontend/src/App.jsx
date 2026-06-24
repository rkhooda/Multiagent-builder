import { useState, useEffect } from 'react'
import axios from 'axios'

function App() {
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

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

  return (
    <div className="min-h-screen bg-[#0d0e12] text-[#f3f4f6] flex flex-col items-center justify-center p-6 relative overflow-hidden font-sans">
      {/* Background Gradients */}
      <div className="absolute top-[-20%] left-[-10%] w-[500px] h-[500px] rounded-full bg-purple-900/20 blur-[120px] pointer-events-none" />
      <div className="absolute bottom-[-20%] right-[-10%] w-[500px] h-[500px] rounded-full bg-blue-900/20 blur-[120px] pointer-events-none" />

      {/* Main Glassmorphic Card */}
      <div className="w-full max-w-lg bg-[#16171d]/80 backdrop-blur-xl border border-white/10 rounded-2xl p-8 shadow-2xl transition-all duration-300 hover:border-purple-500/30">
        
        {/* Header / Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center p-3 bg-purple-500/10 border border-purple-500/20 rounded-xl mb-4 shadow-inner">
            <svg className="w-8 h-8 text-purple-400 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path>
            </svg>
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight bg-gradient-to-r from-purple-400 via-indigo-400 to-blue-400 bg-clip-text text-transparent">
            Multi-Agent AI Builder
          </h1>
          <p className="text-sm text-gray-400 mt-2">
            Autonomously building your ideas into reality.
          </p>
        </div>

        {/* Status Section */}
        <div className="space-y-6">
          <div className="bg-[#1f2028] border border-white/5 rounded-xl p-6">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">
              System Gateway Status
            </h2>
            
            {loading ? (
              <div className="flex items-center space-x-3 text-purple-400">
                <svg className="animate-spin h-5 w-5 text-purple-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span className="text-sm font-medium">Connecting to backend...</span>
              </div>
            ) : error ? (
              <div className="flex items-start space-x-3 text-rose-400">
                <svg className="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
                </svg>
                <div>
                  <span className="text-sm font-medium block">Backend offline</span>
                  <span className="text-xs text-rose-400/70 mt-1 block font-mono">{error}</span>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-3">
                  <span className="relative flex h-3 w-3">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                  </span>
                  <div>
                    <span className="text-sm font-semibold text-emerald-400 block">Backend status: {health?.status}</span>
                    <span className="text-xs text-gray-400 block mt-0.5">Version: {health?.version}</span>
                  </div>
                </div>
                <div className="px-3 py-1 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-full text-xs font-semibold">
                  Online
                </div>
              </div>
            )}
          </div>

          {/* Footer Info */}
          <div className="flex justify-between items-center text-xs text-gray-500 border-t border-white/5 pt-6">
            <span>Day 1 Foundation Setup</span>
            <div className="flex items-center space-x-2">
              <span className="w-1.5 h-1.5 bg-purple-500 rounded-full"></span>
              <span>React + FastAPI Setup</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
