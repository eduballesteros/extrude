/**
 * Lupa de la herramienta de medir: amplía la zona bajo el cursor y marca el punto exacto que se
 * usará al hacer clic, ajustado al vértice o al borde más cercano del triángulo pulsado.
 *
 * - `snapHit` decide el punto (vértice, borde o superficie). Lo usan por igual la lupa y el clic,
 *   así que lo que se ve es lo que se mide.
 * - `Loupe` es un lienzo 2D sin eventos de puntero (`pointer-events: none`): no intercepta clics
 *   ni mueve la cámara. Copia el fotograma recién dibujado del visor, en píxeles reales del
 *   lienzo, por lo que funciona con cualquier escala del navegador o densidad de pantalla.
 */
import {
  type BufferAttribute,
  type Camera,
  type InstancedMesh,
  type Intersection,
  Matrix4,
  type Mesh,
  type Ray,
  Vector3,
} from 'three';

export type SnapKind = 'vertex' | 'edge' | 'surface';

export interface SnapResult {
  point: Vector3;
  kind: SnapKind;
}

export const SNAP_VERTEX_PX = 12;
export const SNAP_EDGE_PX = 7;
export const LOUPE_SIZE_PX = 152;
export const LOUPE_ZOOM = 4;
const LOUPE_GAP_PX = 22;

export const SNAP_LABEL: Record<SnapKind, string> = {
  vertex: 'Vértice',
  edge: 'Borde',
  surface: 'Superficie',
};

interface Size {
  width: number;
  height: number;
}

interface Point2 {
  x: number;
  y: number;
}

/** Posición en píxeles CSS del visor de un punto de la escena. */
export function toScreen(point: Vector3, camera: Camera, viewport: Size): Point2 {
  const ndc = point.clone().project(camera);
  return { x: ((ndc.x + 1) / 2) * viewport.width, y: ((1 - ndc.y) / 2) * viewport.height };
}

/** Vértices en coordenadas de mundo del triángulo impactado (incluida la copia instanciada). */
export function hitTriangle(hit: Intersection): [Vector3, Vector3, Vector3] | null {
  const mesh = hit.object as Mesh;
  const position = mesh.geometry?.attributes['position'] as BufferAttribute | undefined;
  if (!hit.face || !position) return null;
  const matrix = new Matrix4();
  const instanced = mesh as unknown as InstancedMesh;
  if (instanced.isInstancedMesh && hit.instanceId !== undefined) {
    instanced.getMatrixAt(hit.instanceId, matrix);
    matrix.premultiply(mesh.matrixWorld);
  } else {
    matrix.copy(mesh.matrixWorld);
  }
  const vertex = (index: number): Vector3 =>
    new Vector3().fromBufferAttribute(position, index).applyMatrix4(matrix);
  return [vertex(hit.face.a), vertex(hit.face.b), vertex(hit.face.c)];
}

/**
 * Punto que se medirá: el vértice del triángulo a menos de `SNAP_VERTEX_PX` del cursor; si no,
 * el punto del borde más cercano a menos de `SNAP_EDGE_PX`; si no, el punto de la superficie.
 */
export function snapHit(
  hit: Intersection,
  ray: Ray,
  camera: Camera,
  cursor: Point2,
  viewport: Size,
): SnapResult {
  const triangle = hitTriangle(hit);
  if (!triangle) return { point: hit.point.clone(), kind: 'surface' };
  let best: { point: Vector3; distance: number } | null = null;
  for (const vertex of triangle) {
    const screen = toScreen(vertex, camera, viewport);
    const distance = Math.hypot(screen.x - cursor.x, screen.y - cursor.y);
    if (distance <= SNAP_VERTEX_PX && (!best || distance < best.distance)) {
      best = { point: vertex, distance };
    }
  }
  if (best) return { point: best.point.clone(), kind: 'vertex' };
  for (let i = 0; i < 3; i++) {
    const onEdge = new Vector3();
    ray.distanceSqToSegment(triangle[i]!, triangle[(i + 1) % 3]!, undefined, onEdge);
    const screen = toScreen(onEdge, camera, viewport);
    const distance = Math.hypot(screen.x - cursor.x, screen.y - cursor.y);
    if (distance <= SNAP_EDGE_PX && (!best || distance < best.distance)) {
      best = { point: onEdge, distance };
    }
  }
  return best ? { point: best.point, kind: 'edge' } : { point: hit.point.clone(), kind: 'surface' };
}

/**
 * Rectángulo del lienzo del visor (en píxeles reales del lienzo) que amplía la lupa: centrado
 * en el cursor. `canvas` son los píxeles del lienzo y `viewport` su tamaño CSS.
 */
export function loupeSource(
  cursor: Point2,
  viewport: Size,
  canvas: Size,
  size = LOUPE_SIZE_PX,
  zoom = LOUPE_ZOOM,
): { x: number; y: number; width: number; height: number } {
  const scaleX = canvas.width / Math.max(viewport.width, 1);
  const scaleY = canvas.height / Math.max(viewport.height, 1);
  const width = (size / zoom) * scaleX;
  const height = (size / zoom) * scaleY;
  return { x: cursor.x * scaleX - width / 2, y: cursor.y * scaleY - height / 2, width, height };
}

/** Esquina superior izquierda de la lupa: junto al cursor, sin salirse del visor ni taparlo. */
export function loupePosition(cursor: Point2, viewport: Size, size = LOUPE_SIZE_PX): Point2 {
  let x = cursor.x + LOUPE_GAP_PX;
  let y = cursor.y + LOUPE_GAP_PX;
  if (x + size > viewport.width) x = cursor.x - LOUPE_GAP_PX - size;
  if (y + size > viewport.height) y = cursor.y - LOUPE_GAP_PX - size;
  return {
    x: Math.min(Math.max(x, 0), Math.max(viewport.width - size, 0)),
    y: Math.min(Math.max(y, 0), Math.max(viewport.height - size, 0)),
  };
}

export class Loupe {
  readonly element = document.createElement('div');
  private readonly canvas = document.createElement('canvas');
  private readonly caption = document.createElement('span');

  constructor(container: HTMLElement) {
    this.element.className = 'measure-loupe';
    this.element.hidden = true;
    this.element.setAttribute('aria-hidden', 'true');
    this.caption.className = 'measure-loupe-caption';
    this.element.append(this.canvas, this.caption);
    container.append(this.element);
  }

  hide(): void {
    this.element.hidden = true;
  }

  /**
   * Dibuja la lupa con el fotograma actual de `source` (llamar justo después de renderizar).
   * `target` es la posición en pantalla del punto que se usará, o null si no hay geometría.
   */
  draw(
    source: HTMLCanvasElement,
    cursor: Point2,
    viewport: Size,
    target: { screen: Point2; kind: SnapKind } | null,
  ): void {
    const ratio = Math.max(source.width / Math.max(viewport.width, 1), 1);
    const pixels = Math.round(LOUPE_SIZE_PX * ratio);
    if (this.canvas.width !== pixels) {
      this.canvas.width = pixels;
      this.canvas.height = pixels;
    }
    const ctx = this.canvas.getContext('2d');
    if (!ctx) return;
    const rect = loupeSource(cursor, viewport, { width: source.width, height: source.height });
    ctx.fillStyle = '#1b2233';
    ctx.fillRect(0, 0, pixels, pixels);
    ctx.imageSmoothingEnabled = false; // píxeles nítidos: se distinguen aristas y vértices
    ctx.drawImage(source, rect.x, rect.y, rect.width, rect.height, 0, 0, pixels, pixels);

    const center = pixels / 2;
    const line = Math.max(Math.round(ratio), 1);
    // Punto de mira: negro con filo blanco para leerse sobre cualquier fondo
    for (const [color, width] of [
      ['rgb(255 255 255 / 85%)', line * 3],
      ['rgb(7 17 44 / 90%)', line],
    ] as const) {
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.beginPath();
      const gap = 6 * ratio;
      for (const [dx, dy] of [
        [1, 0],
        [-1, 0],
        [0, 1],
        [0, -1],
      ] as const) {
        ctx.moveTo(center + dx * gap, center + dy * gap);
        ctx.lineTo(center + dx * center, center + dy * center);
      }
      ctx.stroke();
    }
    if (target) {
      // Punto exacto que se medirá (puede no coincidir con el cursor si se ajusta a un vértice)
      const x = center + (target.screen.x - cursor.x) * LOUPE_ZOOM * ratio;
      const y = center + (target.screen.y - cursor.y) * LOUPE_ZOOM * ratio;
      const radius = (target.kind === 'surface' ? 3 : 5) * ratio;
      ctx.beginPath();
      if (target.kind === 'vertex') ctx.rect(x - radius, y - radius, radius * 2, radius * 2);
      else ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = target.kind === 'surface' ? '#ffffff' : '#d9744a';
      ctx.fill();
      ctx.lineWidth = line * 1.5;
      ctx.strokeStyle = '#07112c';
      ctx.stroke();
    }
    this.caption.textContent = target ? SNAP_LABEL[target.kind] : 'Sin geometría';
    this.element.dataset['snap'] = target?.kind ?? 'none';
    const position = loupePosition(cursor, viewport);
    this.element.style.transform = `translate(${position.x}px, ${position.y}px)`;
    this.element.hidden = false;
  }
}
