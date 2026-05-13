import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react()],
  // `@icons` resolves to src/icons so the Maiko pixel-art icon module is
  // a one-line import from anywhere in the tree. Importing from "@icons"
  // gets you our customized icons (Paw, Hearth, X, Plus, etc.) plus a
  // pass-through to lucide-react for everything we haven't ported yet.
  resolve: {
    alias: {
      '@icons': fileURLToPath(new URL('./src/icons', import.meta.url)),
    },
  },
  build: {
    outDir: '../src/planet_maiko/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8420',
    },
  },
})
