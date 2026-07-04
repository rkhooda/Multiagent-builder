import { useState, useEffect, useRef } from 'react'

export function useProjectStream(projectId) {
  const [events, setEvents] = useState([])
  const [status, setStatus] = useState('connecting')
  const ws = useRef(null)

  useEffect(() => {
    if (!projectId) return
    
    let isMounted = true
    let reconnectTimeout = null

    const connect = () => {
      if (!isMounted) return
      
      // Close existing socket if open
      if (ws.current) {
        ws.current.close()
      }

      console.log(`Connecting to WebSocket for project: ${projectId}`)
      ws.current = new WebSocket(`ws://localhost:8000/ws/projects/${projectId}`)
      
      ws.current.onopen = () => {
        if (!isMounted) return
        setStatus('connected')
      }
      
      ws.current.onmessage = (e) => {
        if (!isMounted) return
        const event = JSON.parse(e.data)
        if (event.type === 'heartbeat') return  // ignore keepalives
        
        setEvents(prev => {
          // Avoid duplicate events if any
          const exists = prev.some(existing => 
            existing.type === event.type &&
            existing.agent === event.agent &&
            existing.stage === event.stage &&
            existing.preview === event.preview &&
            existing.content === event.content
          )
          if (exists) return prev
          return [...prev, { ...event, timestamp: new Date().toISOString() }]
        })

        if (event.type === 'gate_reached') setStatus('awaiting_approval')
        if (event.type === 'pipeline_complete') setStatus('done')
        if (event.type === 'error') setStatus('error')
      }
      
      ws.current.onclose = () => {
        if (!isMounted) return
        setStatus('reconnecting')
        reconnectTimeout = setTimeout(connect, 2000)  // auto-reconnect after 2 seconds
      }
      
      ws.current.onerror = () => {
        if (!isMounted) return
        setStatus('error')
      }
    }
    
    connect()
    
    return () => {
      isMounted = false
      if (reconnectTimeout) clearTimeout(reconnectTimeout)
      if (ws.current) {
        ws.current.close()
      }
    }
  }, [projectId])

  const resumePipeline = async (decision, feedback = '') => {
    try {
      const res = await fetch(`http://localhost:8000/api/projects/${projectId}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, feedback })
      })
      if (!res.ok) {
        console.error('Failed to resume pipeline')
      }
    } catch (err) {
      console.error('Error resuming pipeline:', err)
    }
  }

  return { events, setEvents, status, setStatus, resumePipeline }
}
