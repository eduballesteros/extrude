import type { WebGLRenderer } from 'three';

const SAMPLE_WINDOW_MS = 1000;

/** Indicador de FPS (media por segundo), triángulos y draw calls del último fotograma. */
export class StatsOverlay {
  readonly element: HTMLElement;
  private frames = 0;
  private windowStart = performance.now();
  private fps = 0;

  constructor(private readonly renderer: WebGLRenderer) {
    this.element = document.createElement('div');
    this.element.className = 'stats';
    this.element.setAttribute('data-testid', 'stats');
  }

  /** Llamar una vez por fotograma, después de renderizar. */
  update(now: number): void {
    this.frames += 1;
    const elapsed = now - this.windowStart;
    if (elapsed < SAMPLE_WINDOW_MS) return;
    this.fps = (this.frames * 1000) / elapsed;
    this.frames = 0;
    this.windowStart = now;
    const { triangles, calls } = this.renderer.info.render;
    this.element.dataset['fps'] = this.fps.toFixed(1);
    this.element.dataset['triangles'] = String(triangles);
    this.element.dataset['calls'] = String(calls);
    this.element.textContent = `${this.fps.toFixed(0)} FPS · ${triangles.toLocaleString('es-ES')} tri · ${calls} draw calls`;
  }
}
