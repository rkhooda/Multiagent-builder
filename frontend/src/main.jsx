import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { applyTheme, storedTheme } from './lib/theme'

// Applied before the first paint, not in an effect: setting the theme after
// mount makes a light-mode user watch a dark frame flash and then swap.
applyTheme(storedTheme())

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
