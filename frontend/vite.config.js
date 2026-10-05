import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/scenarios': 'http://localhost:8000',
      '/runs': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
