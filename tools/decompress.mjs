// Decodifica un GLB comprimido (Draco, Meshopt, KHR_mesh_quantization) a un GLB editable:
// atributos en float sin cuantizar, sin extensiones de compresión y sin transformaciones de nodo
// en las mallas fusionadas (se hornean en los vértices). Conserva extras, _OBJECT_ID y _SURFACE.
// Uso: node decompress.mjs entrada.glb salida.glb
import { NodeIO, VertexLayout } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { clearNodeTransform, dequantize } from '@gltf-transform/functions';
import draco3d from 'draco3dgltf';
import { MeshoptDecoder } from 'meshoptimizer';

const [source, target] = process.argv.slice(2);
if (!source || !target) {
  console.error('Uso: node decompress.mjs entrada.glb salida.glb');
  process.exit(2);
}
await MeshoptDecoder.ready;
const io = new NodeIO()
  .setVertexLayout(VertexLayout.SEPARATE) // Extrude no lee datos intercalados
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({
  'draco3d.decoder': await draco3d.createDecoderModule(),
  'meshopt.decoder': MeshoptDecoder,
});

const doc = await io.read(source);
await doc.transform(dequantize({ pattern: /.*/ }));
const root = doc.getRoot();
for (const node of root.listNodes()) {
  // La cuantización deja escala/traslación en los nodos con malla: se hornean (solo mallas sin
  // instancias; las instancias conservan su TRS por copia y su nodo padre queda igual)
  const instanced = node.getExtension('EXT_mesh_gpu_instancing');
  if (node.getMesh() && !instanced && node.listChildren().length === 0) clearNodeTransform(node);
}
for (const name of [
  'KHR_draco_mesh_compression',
  'EXT_meshopt_compression',
  'KHR_mesh_quantization',
]) {
  root
    .listExtensionsUsed()
    .filter((e) => e.extensionName === name)
    .forEach((e) => e.dispose());
}
await io.write(target, doc);
const left = root.listNodes().filter((n) => {
  const [tx, ty, tz] = n.getTranslation();
  const [sx, sy, sz] = n.getScale();
  return n.getMesh() && !n.getExtension('EXT_mesh_gpu_instancing') &&
    (tx || ty || tz || sx !== 1 || sy !== 1 || sz !== 1);
}).length;
console.log(JSON.stringify({ ok: true, transformedMeshNodes: left }));
