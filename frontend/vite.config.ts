import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The backend URL is configurable so the PoC can run beside other local apps.
const apiTarget = process.env.TE_API_URL ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
});
