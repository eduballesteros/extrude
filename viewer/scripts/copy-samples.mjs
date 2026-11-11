import { copyFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const source = new URL('../../samples/glb/', import.meta.url);
const destination = new URL('../public/models/', import.meta.url);
await mkdir(destination, { recursive: true });
for (const [from, to] of [
  ['block_2d.glb', 'block_2d.glb'],
  ['block_2d_utm.glb', 'block_2d_utm.glb'],
  ['nested_blocks.glb', 'nested.glb'],
]) {
  await copyFile(new URL(from, source), new URL(to, destination));
}
console.log(`Tres modelos de ejemplo preparados en ${fileURLToPath(destination)}`);
