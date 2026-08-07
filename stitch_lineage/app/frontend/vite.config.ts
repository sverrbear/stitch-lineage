import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// base './' -> relative asset paths, so the built app works both under
// `stitch serve` (FastAPI static mount) and file-based static hosting
// (`stitch export --site`), at any URL prefix.
export default defineConfig(({ command }) => ({
  base: './',
  plugins: [react()],
  // dev-public holds the (gitignored) dev graph used when no local API is
  // running. It must never leak into dist/, so publicDir is dev-only.
  publicDir: command === 'serve' ? 'dev-public' : false,
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
  },
}))
