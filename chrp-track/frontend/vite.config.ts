import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In dev, Vite forwards API and WebSocket calls to FastAPI on :8000,
// so the app always talks to its own origin (same as behind nginx in Docker).
const backend = process.env.BACKEND_URL ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
    },
  },
});
