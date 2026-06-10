import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies API + ADK chat routes to the FastAPI backend (port 8000).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/run_sse': 'http://127.0.0.1:8000',
      '/apps': 'http://127.0.0.1:8000',
    },
  },
})
