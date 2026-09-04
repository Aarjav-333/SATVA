import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Proxy the API in development so the browser makes same-origin requests
    // and CORS never enters the picture during a demo.
    proxy: {
      '/api': {
        target: process.env.SATVA_API_ORIGIN || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // MapLibre and Recharts are large and each used on only some routes.
        // Splitting them keeps the initial load small on the kind of
        // connection a district office actually has.
        manualChunks: {
          maplibre: ['maplibre-gl'],
          charts: ['recharts'],
          react: ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
})
