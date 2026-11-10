import { frameRect } from '../views/viewState';

/** Superpone el encuadre exacto del render (relación de aspecto elegida) sobre el visor. */
export class FrameOverlay {
  readonly element = document.createElement('div');
  private aspect = 16 / 9;
  private visible = true;

  constructor(private readonly container: HTMLElement) {
    this.element.className = 'camera-frame';
    this.element.setAttribute('data-testid', 'camera-frame');
    container.appendChild(this.element);
    new ResizeObserver(() => this.layout()).observe(container);
  }

  set(aspect: number, visible: boolean): void {
    this.aspect = aspect;
    this.visible = visible;
    this.layout();
  }

  private layout(): void {
    const { clientWidth: w, clientHeight: h } = this.container;
    const rect = frameRect(w, h, this.aspect);
    this.element.hidden = !this.visible;
    Object.assign(this.element.style, {
      left: `${rect.x}px`,
      top: `${rect.y}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
    });
  }
}
