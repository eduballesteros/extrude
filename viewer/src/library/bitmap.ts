/** Operaciones de imagen en el navegador: dimensiones reales, miniaturas y reescalado. */

const THUMBNAIL_WIDTH = 320;
/** Lado mayor de la imagen que se envía como referencia a un proveedor de IA. */
export const REFERENCE_MAX_SIDE = 2048;

export class ImageDecodeError extends Error {}

async function decode(blob: Blob): Promise<ImageBitmap> {
  try {
    return await createImageBitmap(blob);
  } catch {
    throw new ImageDecodeError('El archivo no es una imagen que el navegador pueda abrir.');
  }
}

function canvasOf(width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function toBlob(canvas: HTMLCanvasElement, mime: string, quality?: number): Promise<Blob> {
  return new Promise((resolve, reject) =>
    canvas.toBlob(
      (blob) =>
        blob
          ? resolve(blob)
          : reject(
              new Error(
                `El navegador no pudo crear una imagen de ${canvas.width}×${canvas.height} (memoria insuficiente). Prueba una resolución menor.`,
              ),
            ),
      mime,
      quality,
    ),
  );
}

export interface ImageFacts {
  width: number;
  height: number;
  thumbnail: string;
}

/** Dimensiones reales y miniatura JPEG de una imagen. */
export async function inspectImage(blob: Blob): Promise<ImageFacts> {
  const bitmap = await decode(blob);
  try {
    const width = Math.min(THUMBNAIL_WIDTH, bitmap.width);
    const height = Math.max(1, Math.round((width * bitmap.height) / bitmap.width));
    const canvas = canvasOf(width, height);
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, width, height);
    return {
      width: bitmap.width,
      height: bitmap.height,
      thumbnail: canvas.toDataURL('image/jpeg', 0.8),
    };
  } finally {
    bitmap.close();
  }
}

/** Reescala a `width × height` con el mejor filtro del navegador (PNG). */
export async function resizeImage(blob: Blob, width: number, height: number): Promise<Blob> {
  const bitmap = await decode(blob);
  try {
    const canvas = canvasOf(width, height);
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('No se pudo crear el lienzo para reescalar.');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(bitmap, 0, 0, width, height);
    return await toBlob(canvas, 'image/png');
  } finally {
    bitmap.close();
  }
}

/** Tamaño al reducir una imagen para que su lado mayor no pase de `maxSide` (sin ampliar). */
export function fitSide(
  width: number,
  height: number,
  maxSide = REFERENCE_MAX_SIDE,
): { width: number; height: number } {
  const scale = Math.min(1, maxSide / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/** Imagen de referencia para un proveedor: la captura, reducida si pasa de 2048 px de lado. */
export async function referenceImage(
  blob: Blob,
  width: number,
  height: number,
): Promise<{ blob: Blob; width: number; height: number }> {
  const size = fitSide(width, height);
  if (size.width === width && size.height === height) return { blob, width, height };
  return { blob: await resizeImage(blob, size.width, size.height), ...size };
}

export async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}
