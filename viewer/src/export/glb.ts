/** Lectura y escritura del contenedor GLB (cabecera + chunk JSON + chunk BIN opcional). */

const MAGIC = 0x46546c67; // "glTF"
const VERSION = 2;
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;

export type GltfJson = Record<string, unknown> & {
  scene?: number;
  scenes?: { nodes?: number[]; [key: string]: unknown }[];
  nodes?: Record<string, unknown>[];
  cameras?: Record<string, unknown>[];
  extensions?: Record<string, unknown>;
  extensionsUsed?: string[];
};

export interface GlbParts {
  json: GltfJson;
  bin: Uint8Array | null;
}

export class GlbError extends Error {}

export function parseGlb(buffer: ArrayBuffer): GlbParts {
  const view = new DataView(buffer);
  if (buffer.byteLength < 20 || view.getUint32(0, true) !== MAGIC) {
    throw new GlbError(
      'No es un archivo GLB (falta la cabecera glTF). Exporta el modelo como .glb.',
    );
  }
  if (view.getUint32(4, true) !== VERSION) throw new GlbError('Solo se admite glTF 2.0.');
  const jsonLength = view.getUint32(12, true);
  if (view.getUint32(16, true) !== CHUNK_JSON) throw new GlbError('El primer chunk no es JSON.');
  const json = JSON.parse(
    new TextDecoder().decode(new Uint8Array(buffer, 20, jsonLength)),
  ) as GltfJson;
  let bin: Uint8Array | null = null;
  const binHeader = 20 + jsonLength;
  if (binHeader + 8 <= buffer.byteLength && view.getUint32(binHeader + 4, true) === CHUNK_BIN) {
    bin = new Uint8Array(buffer, binHeader + 8, view.getUint32(binHeader, true));
  }
  return { json, bin };
}

function padded(bytes: Uint8Array, fill: number): Uint8Array {
  const length = Math.ceil(bytes.length / 4) * 4;
  if (length === bytes.length) return bytes;
  const out = new Uint8Array(length).fill(fill);
  out.set(bytes);
  return out;
}

export function encodeGlb({ json, bin }: GlbParts): ArrayBuffer {
  const jsonBytes = padded(new TextEncoder().encode(JSON.stringify(json)), 0x20);
  const binBytes = bin ? padded(bin, 0) : null;
  const total = 12 + 8 + jsonBytes.length + (binBytes ? 8 + binBytes.length : 0);
  const out = new ArrayBuffer(total);
  const view = new DataView(out);
  const bytes = new Uint8Array(out);
  view.setUint32(0, MAGIC, true);
  view.setUint32(4, VERSION, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonBytes.length, true);
  view.setUint32(16, CHUNK_JSON, true);
  bytes.set(jsonBytes, 20);
  if (binBytes) {
    const offset = 20 + jsonBytes.length;
    view.setUint32(offset, binBytes.length, true);
    view.setUint32(offset + 4, CHUNK_BIN, true);
    bytes.set(binBytes, offset + 8);
  }
  return out;
}
