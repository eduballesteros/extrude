// Decodifica un GLB (también Draco/Meshopt) y resume _OBJECT_ID y POSITION por malla.
// Uso: node attributes.mjs archivo.glb  → JSON [{mesh, vertices, ids: {id: nº vértices}, min, max}]
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import draco3d from 'draco3dgltf';
import { MeshoptDecoder } from 'meshoptimizer';

await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'draco3d.decoder': await draco3d.createDecoderModule(),
  'meshopt.decoder': MeshoptDecoder,
});

const doc = await io.read(process.argv[2]);
const out = [];
for (const mesh of doc.getRoot().listMeshes()) {
  for (const prim of mesh.listPrimitives()) {
    const pos = prim.getAttribute('POSITION');
    const idAttr = prim.getAttribute('_OBJECT_ID');
    const ids = {};
    if (idAttr) {
      const el = [];
      for (let i = 0; i < idAttr.getCount(); i++) {
        const id = Math.round(idAttr.getElement(i, el)[0]);
        ids[id] = (ids[id] ?? 0) + 1;
      }
    }
    // Posiciones dequantizadas (KHR_mesh_quantization normaliza; aplicamos la transformación del nodo)
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    const node = doc.getRoot().listNodes().find((n) => n.getMesh() === mesh);
    const m = node ? node.getWorldMatrix() : [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    const el = [];
    for (let i = 0; i < pos.getCount(); i++) {
      const [x, y, z] = pos.getElement(i, el);
      const w = [
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
      ];
      for (let k = 0; k < 3; k++) {
        min[k] = Math.min(min[k], w[k]);
        max[k] = Math.max(max[k], w[k]);
      }
    }
    out.push({ mesh: mesh.getName(), mode: prim.getMode(), vertices: pos.getCount(), ids, min, max });
  }
}
console.log(JSON.stringify(out));
