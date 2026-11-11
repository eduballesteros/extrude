import { Quaternion } from 'three';
import type { App } from '../app';
import { northScreenAngle, projectCube } from '../scene/viewCube';
import { button, h } from './dom';

const SVG_NS = 'http://www.w3.org/2000/svg';
const SIZE = 120;
const CUBE = 40; // lado proyectado del cubo (px)
const RING = 48; // radio del anillo con la aguja del norte

const FACE_TITLES: Record<string, string> = {
  top: 'Planta (TOP)',
  north: 'Alzado norte (BACK)',
  south: 'Alzado sur (FRONT)',
  east: 'Alzado este (RIGHT)',
  west: 'Alzado oeste (LEFT)',
};

function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

/** Cubo de orientación (planta, alzados, isométrica) con flecha del norte real. */
export class ViewCubeWidget {
  readonly element: HTMLElement;
  private readonly svg = svgEl('svg', {
    viewBox: `${-SIZE / 2} ${-SIZE / 2} ${SIZE} ${SIZE}`,
    width: String(SIZE),
    height: String(SIZE),
  });
  private readonly faces = svgEl('g', {});
  private readonly needle = svgEl('g', { class: 'north-needle' });
  private readonly last = new Quaternion(2, 2, 2, 2);
  private lastNorth = Number.NaN;

  constructor(private readonly app: App) {
    this.svg.append(
      svgEl('circle', { r: String(RING), class: 'cube-ring' }),
      this.needle,
      this.faces,
    );
    this.needle.append(
      svgEl('path', {
        d: `M0 ${-RING - 5} L3.5 ${-RING + 2} L-3.5 ${-RING + 2} Z`,
        class: 'needle-head',
      }),
      svgEl('text', { y: String(-RING - 8), class: 'needle-label' }),
    );
    (this.needle.lastChild as SVGTextElement).textContent = 'N';
    const iso = button({
      label: 'ISO',
      title: 'Vista isométrica',
      variant: 'ghost',
      size: 'sm',
      onClick: () => app.viewFrom('iso'),
    });
    iso.classList.add('cube-iso');
    this.element = h('div', {
      class: 'viewcube',
      attrs: { 'aria-label': 'Orientación de la cámara' },
    });
    this.element.append(this.svg, iso);
    const tick = (): void => {
      this.update();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  private update(): void {
    const q = this.app.nav.camera.quaternion;
    const north = this.app.state.sun.northRotationDeg;
    if (this.last.angleTo(q) < 1e-4 && north === this.lastNorth) return;
    this.last.copy(q);
    this.lastNorth = north;
    this.faces.replaceChildren(
      ...projectCube(q, CUBE).map((face) => {
        const g = svgEl('g', { class: `cube-face face-${face.id}`, role: 'button', tabindex: '0' });
        const title = svgEl('title', {});
        title.textContent = FACE_TITLES[face.id] ?? face.label;
        g.append(
          title,
          svgEl('polygon', {
            points: face.points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' '),
          }),
        );
        const text = svgEl('text', { x: face.center[0].toFixed(1), y: face.center[1].toFixed(1) });
        text.textContent = face.label;
        g.append(text);
        const go = (): void => this.app.viewFrom(face.id);
        g.addEventListener('click', go);
        g.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') go();
        });
        return g;
      }),
    );
    this.needle.setAttribute('transform', `rotate(${northScreenAngle(q, north).toFixed(1)})`);
  }
}
