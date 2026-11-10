// Valida uno o varios .glb con el validador oficial de Khronos.
// Uso: node validate.mjs archivo.glb [...]  → imprime JSON con el resumen y sale con 1 si hay errores.
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const validator = require('gltf-validator');

const files = process.argv.slice(2);
if (files.length === 0) {
  console.error('Uso: node validate.mjs archivo.glb [...]');
  process.exit(2);
}

let failed = false;
const results = [];
for (const file of files) {
  const report = await validator.validateBytes(new Uint8Array(await readFile(file)), {
    maxIssues: 50,
  });
  const { numErrors, numWarnings, numInfos, numHints } = report.issues;
  failed ||= numErrors > 0;
  results.push({
    file,
    errors: numErrors,
    warnings: numWarnings,
    infos: numInfos,
    hints: numHints,
    messages: report.issues.messages.map((m) => `${m.severity} ${m.code} ${m.pointer ?? ''}: ${m.message}`),
  });
}
console.log(JSON.stringify(results, null, 2));
process.exit(failed ? 1 : 0);
