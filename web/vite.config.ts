import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendHost = process.env.QUANT_BACKEND_PROXY_HOST ?? '127.0.0.1';
const backendPort = process.env.QUANT_BACKEND_PORT ?? '8001';

export default defineConfig({
  plugins: [react()],
  build: {
    modulePreload: {
      polyfill: false,
    },
  },
  server: {
    proxy: {
      '/api/agent/chat': {
        target: `ws://${backendHost}:${backendPort}`,
        ws: true,
      },
      '/api': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
});
