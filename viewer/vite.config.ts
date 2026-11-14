import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')) as {
  version: string;
};

export default defineConfig({
  server: { port: 5173 },
  // Versión visible en la pantalla de inicio, tomada de package.json (una sola fuente)
  define: { 'import.meta.env.VITE_APP_VERSION': JSON.stringify(pkg.version) },
  // Three.js por sí solo ronda los 420 kB minificado y el chunk principal, los 980 kB: el aviso
  // por defecto (500 kB) sería ruido. Lo que solo se usa a veces (luz de área, HDRI, diálogo de
  // IA) se carga con import() dinámico; si este aviso salta, revisa qué ha entrado en el arranque.
  build: {
    target: 'es2022',
    sourcemap: true,
    chunkSizeWarningLimit: 1000,
    // Dos páginas: el visor y la guía de uso (guia.html)
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        guia: fileURLToPath(new URL('./guia.html', import.meta.url)),
      },
    },
  },
});
