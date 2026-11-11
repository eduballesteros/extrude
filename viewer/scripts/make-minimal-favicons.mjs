// Compatibilidad: todos los formatos se generan desde make-icons.py.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('./make-icons.py', import.meta.url));
const result = spawnSync(
  'uv',
  ['run', '--no-project', '--with', 'pillow==11.3.0', 'python', script],
  {
    stdio: 'inherit',
  },
);
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
