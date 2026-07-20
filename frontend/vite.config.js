import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],

  // Dev only — production is served by nginx, which proxies the same two
  // prefixes to the same backend.
  //
  // Every API call in this app is a RELATIVE url, so it resolves against
  // whatever origin served the page: Vite here, nginx in the container. One
  // code path, and no hostname is baked into the production bundle.
  //
  // The alternative, a VITE_API_URL, is not merely clumsier — it is wrong for
  // this app. Vite freezes env at build time, so an absolute URL only works
  // when the app is opened at exactly that hostname. Open it from a phone at
  // 192.168.1.5:3000 (the Day 27 approve-from-phone path) and a baked-in
  // localhost resolves to the phone, so every request fails.
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})

