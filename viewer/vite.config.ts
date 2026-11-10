import { defineConfig } from 'vite';

export default defineConfig({
  server: { port: 5173 },
  // Three.js por sí solo ronda los 700 kB minificado; el aviso por defecto (500 kB) sería ruido.
  build: { target: 'es2022', sourcemap: true, chunkSizeWarningLimit: 1200 },
});
