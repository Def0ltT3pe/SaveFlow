import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// В dev все /api и /metrics проксируются на бэкенд (по умолчанию localhost:8000).
// В Docker nginx делает то же самое.
const target = process.env.VITE_BACKEND_URL ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: ['.devtunnels.ms'],
    proxy: { '/api': target, '/metrics': target },
  },
});
