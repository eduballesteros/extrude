import type { WebGLRenderer } from 'three';
import { formatNumber, h } from './dom';

const SAMPLE_WINDOW_MS = 1000;

/** Píldora discreta con FPS (media por segundo), triángulos y draw calls del último fotograma. */
export class StatsOverlay {
  readonly element: HTMLElement;
  private readonly fpsEl = h('span', { class: 'stat-fps' });
  private readonly triEl = h('span');
  private readonly callsEl = h('span');
  private frames = 0;
  private windowStart = performance.now();

  constructor(private readonly renderer: WebGLRenderer) {
    this.element = h(
      'div',
      { class: 'stats-pill', data: { testid: 'stats' } },
      this.fpsEl,
      h('span', { class: 'sep' }),
      this.triEl,
      h('span', { class: 'sep' }),
      this.callsEl,
    );
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
    this.fpsEl.textContent = `${fps.toFixed(0)} FPS`;
    this.fpsEl.classList.toggle('is-low', fps < 30);
    this.triEl.textContent = `${formatNumber(triangles)} tri`;
    this.callsEl.textContent = `${calls} draw calls`;
  }
}
