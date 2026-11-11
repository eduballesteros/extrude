import {
  BufferGeometry,
  type Camera,
  Color,
  Group,
  Line,
  LineBasicMaterial,
  LineDashedMaterial,
  Mesh,
  MeshBasicMaterial,
  OrthographicCamera,
  PerspectiveCamera,
  SphereGeometry,
  Vector3,
} from 'three';

/**
 * Medición punto a punto en la escena (metros, Y-up del glTF = Z del dibujo).
 * Clic 1 fija A, clic 2 fija B y muestra la cota; un tercer clic empieza otra medición.
 */

export interface Measurement {
  /** Distancia euclídea 3D (m). */
  distance: number;
  /** Distancia en planta, XY del dibujo = XZ de la escena (m). */
  horizontal: number;
  /** Desnivel B − A en cota (Z del dibujo = Y de la escena), con signo (m). */
  vertical: number;
  /** Pendiente |Δz| / planta en %, o null si los puntos están en la misma vertical. */
  slopePct: number | null;
}

type Point3 = { x: number; y: number; z: number };

const VERTICAL_EPS_M = 1e-9;
/** Dos clics en el mismo sitio (doble clic) no crean una medición de 0 m. */
const DUPLICATE_EPS_M = 1e-6;
const MARKER_PX = 5;
// Naranja del porfolio (algo más intenso para leerse sobre el cielo) y catetos en azul pizarra
const COLOR = new Color('#d9744a');
const LEG_COLOR = new Color('#476185');
const ON_TOP = 999;

export function measureBetween(a: Point3, b: Point3): Measurement {
  const dx = b.x - a.x;
  const dz = b.z - a.z;
  const vertical = b.y - a.y;
  const horizontal = Math.hypot(dx, dz);
  return {
    distance: Math.hypot(horizontal, vertical),
    horizontal,
    vertical,
    slopePct: horizontal > VERTICAL_EPS_M ? (Math.abs(vertical) / horizontal) * 100 : null,
  };
}

export function formatMeters(value: number, signed = false): string {
  const text = `${Math.abs(value).toFixed(2)} m`;
  if (!signed) return text;
  return value > 0.005 ? `+${text}` : value < -0.005 ? `−${text}` : text;
}

/** Filas (etiqueta, valor) de la etiqueta flotante. */
export function measurementRows(m: Measurement): [string, string][] {
  const rows: [string, string][] = [
    ['3D', formatMeters(m.distance)],
    ['Planta', formatMeters(m.horizontal)],
    ['Δz', formatMeters(m.vertical, true)],
  ];
  if (m.slopePct !== null) rows.push(['Pendiente', `${m.slopePct.toFixed(1)} %`]);
  return rows;
}

/** Metros por píxel en `point` para la cámara (para marcadores de tamaño constante). */
export function worldPerPixel(camera: Camera, point: Vector3, heightPx: number): number {
  if (camera instanceof OrthographicCamera) {
    return (camera.top - camera.bottom) / camera.zoom / heightPx;
  }
  if (camera instanceof PerspectiveCamera) {
    const distance = camera.position.distanceTo(point);
    return (2 * distance * Math.tan((camera.fov * Math.PI) / 360)) / camera.zoom / heightPx;
  }
  return 1 / heightPx;
}

function overlayLine(material: LineBasicMaterial): Line {
  const line = new Line(new BufferGeometry(), material);
  line.renderOrder = ON_TOP;
  line.frustumCulled = false;
  return line;
}

export class MeasureTool {
  /** Línea, catetos (planta y desnivel) y marcadores: se dibujan siempre por encima. */
  readonly group = new Group();
  /** Etiqueta HTML sobre el visor; null sin contenedor (tests sin DOM). */
  readonly label: HTMLElement | null = null;
  active = false;
  private points: Vector3[] = [];
  private readonly line = overlayLine(
    new LineBasicMaterial({ color: COLOR, depthTest: false, transparent: true }),
  );
  private readonly legs = overlayLine(
    new LineDashedMaterial({
      color: LEG_COLOR,
      dashSize: 1,
      gapSize: 0.6,
      depthTest: false,
      transparent: true,
    }),
  );
  private readonly markers: Mesh[];
  private readonly rows: HTMLElement | null = null;

  constructor(
    container: HTMLElement | null,
    private readonly onChange: () => void = () => {},
  ) {
    this.group.name = 'measure';
    const geometry = new SphereGeometry(1, 16, 12);
    const material = new MeshBasicMaterial({ color: COLOR, depthTest: false, transparent: true });
    this.markers = [0, 1].map(() => {
      const marker = new Mesh(geometry, material);
      marker.renderOrder = ON_TOP + 1;
      marker.visible = false;
      return marker;
    });
    this.group.add(this.legs, this.line, ...this.markers);
    if (!container) {
      this.sync();
      return;
    }

    this.rows = document.createElement('dl');
    this.rows.className = 'kv';
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'measure-clear';
    close.title = 'Borrar medición (Esc)';
    close.setAttribute('aria-label', close.title);
    close.textContent = '×';
    close.addEventListener('click', (e) => {
      e.stopPropagation();
      this.clear();
    });
    this.label = document.createElement('div');
    this.label.className = 'measure-label';
    this.label.hidden = true;
    this.label.setAttribute('aria-live', 'polite');
    this.label.append(this.rows, close);
    container.append(this.label);
    this.sync();
  }

  get measurement(): Measurement | null {
    const [a, b] = this.points;
    return a && b ? measureBetween(a, b) : null;
  }

  get pointCount(): number {
    return this.points.length;
  }

  setActive(active: boolean): void {
    if (this.active === active) return;
    this.active = active;
    if (!active) this.clear(false);
    this.onChange();
  }

  /** Añade un punto; devuelve la medición si con él queda completa. */
  addPoint(point: Vector3): Measurement | null {
    const last = this.points.at(-1);
    if (last && last.distanceTo(point) < DUPLICATE_EPS_M) return this.measurement;
    if (this.points.length >= 2) this.points = [];
    this.points.push(point.clone());
    this.sync();
    this.onChange();
    return this.measurement;
  }

  /** Borra la medición (la herramienta sigue activa). */
  clear(notify = true): void {
    if (!this.points.length) return;
    this.points = [];
    this.sync();
    if (notify) this.onChange();
  }

  /** Cada fotograma: marcadores de tamaño constante y etiqueta sobre el punto medio. */
  update(camera: Camera, widthPx: number, heightPx: number): void {
    for (const [i, marker] of this.markers.entries()) {
      const point = this.points[i];
      if (!point) continue;
      marker.scale.setScalar(worldPerPixel(camera, point, heightPx) * MARKER_PX);
    }
    const [a, b] = this.points;
    if (!a || !b) return;
    const material = this.legs.material as LineDashedMaterial;
    const dash = worldPerPixel(camera, a.clone().lerp(b, 0.5), heightPx) * 6;
    material.dashSize = dash;
    material.gapSize = dash * 0.6;
    if (!this.label) return;
    const mid = a.clone().lerp(b, 0.5).project(camera);
    const visible = mid.z > -1 && mid.z < 1;
    this.label.hidden = !visible;
    if (!visible) return;
    this.label.style.transform = `translate(${((mid.x + 1) / 2) * widthPx}px, ${((1 - mid.y) / 2) * heightPx}px) translate(-50%, calc(-100% - 10px))`;
  }

  dispose(): void {
    this.line.geometry.dispose();
    this.legs.geometry.dispose();
    (this.line.material as LineBasicMaterial).dispose();
    (this.legs.material as LineDashedMaterial).dispose();
    this.markers[0]!.geometry.dispose();
    (this.markers[0]!.material as MeshBasicMaterial).dispose();
    this.label?.remove();
  }

  private sync(): void {
    const [a, b] = this.points;
    this.markers.forEach((marker, i) => {
      const point = this.points[i];
      marker.visible = point !== undefined;
      if (point) marker.position.copy(point);
    });
    this.line.visible = this.legs.visible = Boolean(a && b);
    if (this.label) this.label.hidden = !(a && b);
    if (!a || !b) return;
    this.line.geometry.setFromPoints([a, b]);
    // Catetos: tramo en planta a la cota de A y tramo vertical hasta B
    const corner = new Vector3(b.x, a.y, b.z);
    this.legs.geometry.setFromPoints([a, corner, b]);
    this.legs.computeLineDistances();
    this.rows?.replaceChildren(
      ...measurementRows(measureBetween(a, b)).flatMap(([key, value]) => {
        const dt = document.createElement('dt');
        dt.textContent = key;
        const dd = document.createElement('dd');
        dd.textContent = value;
        return [dt, dd];
      }),
    );
  }
}
