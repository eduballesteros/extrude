import type { WebGLRenderer } from 'three';
import { compactNumber, h } from './dom';

const SAMPLE_WINDOW_MS = 1000;

/** Barra de estado mono (esquina inferior izquierda): FPS medio por segundo, triángulos y draws. */
export class StatsOverlay {
  readonly element: HTMLElement;
  private readonly fpsEl = h('b');
  private readonly triEl = h('b');
  private readonly callsEl = h('b');
  private frames = 0;
  private windowStart = performance.now();

  constructor(private readonly renderer: WebGLRenderer) {
    const item = (label: string, value: HTMLElement): HTMLElement =>
      h('span', { class: 'status-item' }, `${label} `, value);
    this.element = h(
      'div',
      { class: 'statusbar mono', data: { testid: 'stats' } },
      item('FPS', this.fpsEl),
      item('TRIS', this.triEl),
      item('DRAWS', this.callsEl),
    );
    this.fpsEl.textContent = this.triEl.textContent = this.callsEl.textContent = '—';
  }

  /** Llamar una vez por fotograma, después de renderizar. */
  update(now: number): void {
    this.frames += 1;
    const elapsed = now - this.windowStart;
    if (elapsed < SAMPLE_WINDOW_MS) return;
    const fps = (this.frames * 1000) / elapsed;
    this.frames = 0;
    this.windowStart = now;
    const { triangles, calls } = this.renderer.info.render;
    this.element.dataset['fps'] = fps.toFixed(1);
    this.element.dataset['triangles'] = String(triangles);
    this.element.dataset['calls'] = String(calls);
    this.fpsEl.textContent = fps.toFixed(0);
    this.fpsEl.classList.toggle('is-low', fps < 30);
    this.triEl.textContent = compactNumber(triangles);
    this.callsEl.textContent = String(calls);
  }
}
