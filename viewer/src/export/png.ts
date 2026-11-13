import {
  type Camera,
  OrthographicCamera,
  PerspectiveCamera,
  type Scene,
  Vector2,
  type WebGLRenderer,
} from 'three';
import { frameHeightFraction, frameRect } from '../views/viewState';

const DEG = Math.PI / 180;
const THUMBNAIL_WIDTH = 320;
const THUMBNAIL_QUALITY = 0.8;

export class RenderSizeError extends Error {}

function maxRenderSize(renderer: WebGLRenderer): number {
  const gl = renderer.getContext();
  const dims = gl.getParameter(gl.MAX_VIEWPORT_DIMS) as Int32Array;
  const renderbuffer = gl.getParameter(gl.MAX_RENDERBUFFER_SIZE) as number;
  return Math.min(dims[0] ?? 4096, dims[1] ?? 4096, renderbuffer);
}

/**
 * Renderiza a `width × height` exactamente el encuadre del marco de cámara y devuelve un PNG.
 * El tamaño pedido es el del archivo, sin depender del tamaño de la ventana ni de la densidad
 * de la pantalla. Se redimensiona el lienzo de forma síncrona (render + copia del bitmap en la
 * misma tarea) y se restaura ANTES de esperar la codificación: no hay parpadeo y quien llama
 * puede reponer su estado en cuanto esta función devuelve la promesa.
 */
export async function renderPng(
  renderer: WebGLRenderer,
  scene: Scene,
  camera: PerspectiveCamera | OrthographicCamera,
  width: number,
  height: number,
  beforeRender?: () => void,
): Promise<Blob> {
  const limit = maxRenderSize(renderer);
  if (width > limit || height > limit) {
    throw new RenderSizeError(
      `${width}×${height} supera el máximo de esta GPU (${limit} px por lado). Reduce la resolución.`,
    );
  }
  const canvas = renderer.domElement;
  const previousSize = renderer.getSize(new Vector2());
  const previousRatio = renderer.getPixelRatio();
  const viewportAspect = previousSize.x / previousSize.y;
  const aspect = width / height;
  const k = frameHeightFraction(viewportAspect, aspect);
  const saved = camera.clone();

  if (camera instanceof PerspectiveCamera) {
    camera.fov = (2 * Math.atan(Math.tan((camera.fov * DEG) / 2) * k)) / DEG;
    camera.aspect = aspect;
  } else {
    const half = ((camera.top - camera.bottom) / 2) * k;
    camera.top = half;
    camera.bottom = -half;
    camera.left = -half * aspect;
    camera.right = half * aspect;
  }
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  let encoded: Promise<Blob>;
  try {
    beforeRender?.();
    renderer.render(scene, camera);
    if (renderer.getContext().isContextLost()) {
      throw new RenderSizeError(
        `La tarjeta gráfica se quedó sin memoria al renderizar ${width}×${height}. Cierra otras pestañas o elige una resolución menor.`,
      );
    }
    // toBlob copia el bitmap de forma síncrona; la codificación es asíncrona
    encoded = new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (b) =>
          b
            ? resolve(b)
            : reject(
                new RenderSizeError(
                  `El navegador no pudo crear el PNG de ${width}×${height} (memoria insuficiente). Elige una resolución menor.`,
                ),
              ),
        'image/png',
      ),
    );
  } finally {
    camera.copy(saved as never);
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(previousRatio);
    renderer.setSize(previousSize.x, previousSize.y, false);
  }
  return encoded;
}

/** Miniatura JPEG del encuadre del marco a partir del último fotograma renderizado. */
export function thumbnailFromCanvas(
  renderer: WebGLRenderer,
  scene: Scene,
  camera: Camera,
  aspect: number,
): string {
  renderer.render(scene, camera);
  const source = renderer.domElement;
  const rect = frameRect(source.width, source.height, aspect);
  const out = document.createElement('canvas');
  out.width = THUMBNAIL_WIDTH;
  out.height = Math.round(THUMBNAIL_WIDTH / aspect);
  const ctx = out.getContext('2d');
  if (!ctx) return '';
  ctx.drawImage(source, rect.x, rect.y, rect.width, rect.height, 0, 0, out.width, out.height);
  return out.toDataURL('image/jpeg', THUMBNAIL_QUALITY);
}
