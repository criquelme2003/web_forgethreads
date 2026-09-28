import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// El backend FastAPI sirve el build en /front/* (ver app/main.py serve_spa),
// por eso el base es /front/. En dev, abrir http://localhost:5173/front/parameters
// (las llamadas a /auth y /app se proxyean al backend en :8000).
export default defineConfig({
  plugins: [react()],
  base: '/front/',
  server: {
    port: 5173,
    proxy: {
      '/auth': 'http://localhost:8000',
      '/app': 'http://localhost:8000',
    },
  },
})
