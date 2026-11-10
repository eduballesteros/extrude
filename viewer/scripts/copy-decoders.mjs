// Copia el decodificador Draco de three a public/draco/ (servido localmente, sin CDN).
import { cpSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = resolve(root, 'node_modules/three/examples/jsm/libs/draco/gltf');
const target = resolve(root, 'public/draco');
mkdirSync(target, { recursive: true });
cpSync(source, target, { recursive: true });
