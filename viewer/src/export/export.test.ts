import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { PerspectiveCamera, Quaternion, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { sunDirection } from '../scene/sun';
import { defaultState, type Vec3 } from '../state';
import { createView, type SceneView, sceneToBlender } from '../views/viewState';
import { generateBlenderScript, lensFromFov, toBlenderData, toBlenderView } from './blenderScript';
import { encodeGlb, parseGlb } from './glb';
import { addCamerasAndLights, LIGHTS_EXTENSION, lookAtQuaternion } from './gltfWithLights';

const REPO = resolve(__dirname, '../../..');
const SAMPLE_GLB = join(REPO, 'samples/glb/nested_blocks.glb');
const ORIGIN: Vec3 = [-25.75, 48.25, 0];
const MODEL = { file: 'model.glb', originOffset: ORIGIN, crs: null };

function views(): SceneView[] {
  const a = defaultState();
  a.camera.position = [80, 60, 80];
  a.camera.target = [0, 0, 0];
  a.layers = {
    EDIFICIOS: { visible: true },
    VEGETACION: { visible: true },
    MOBILIARIO: { visible: true },
  };
  a.lights.push(
    {
      id: 's',
      type: 'spot',
      position: [0, 20, 0],
      target: [0, 0, 0],
      color: '#ffd8a0',
      intensity: 2000,
      angleDeg: 30,
    },
    {
      id: 'a',
      type: 'area',
      position: [10, 5, 0],
      target: [0, 0, 0],
      color: '#ffffff',
      intensity: 10,
      width: 4,
      height: 2,
    },
  );
  const b = defaultState();
  b.camera = {
    ...b.camera,
    type: 'orthographic',
    position: [0, 200, 0.01],
    target: [0, 0, 0],
    orthoSize: 60,
  };
  b.layers = {
    EDIFICIOS: { visible: true },
    VEGETACION: { visible: false },
    MOBILIARIO: { visible: true },
  };
  b.sun.isoDate = '2024-06-21T19:00:00+02:00';
  b.sun.azimuthDeg = 285;
  b.sun.elevationDeg = 25;
  b.tonemapping = { type: 'neutral', exposure: 2 };
  return [
    createView(a, "Perspectiva ''' con comillas", { width: 1920, height: 1080 }),
    createView(b, 'Planta', { width: 2048, height: 2048 }),
  ];
}

const forward = (q: number[]) => new Vector3(0, 0, -1).applyQuaternion(new Quaternion(...q));

describe('lookAtQuaternion', () => {
  it('orienta −Z hacia el objetivo, también en vertical', () => {
    for (const [pos, target] of [
      [
        [10, 5, 10],
        [0, 0, 0],
      ],
      [
        [0, 100, 0],
        [0, 0, 0],
      ],
      [
        [3, 0, 0],
        [3, 0, -8],
      ],
    ] as [Vec3, Vec3][]) {
      const dir = new Vector3(...target).sub(new Vector3(...pos)).normalize();
      expect(forward(lookAtQuaternion(pos, target)).dot(dir)).toBeCloseTo(1, 6);
    }
  });
});

describe('GLB con cámaras y luces', () => {
  const source = readFileSync(SAMPLE_GLB);
  const buffer = source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength);
  const all = views();
  const result = addCamerasAndLights(buffer, all, all[0]!);
  const { json, bin } = parseGlb(result.glb);

  it('conserva intacto el chunk binario', () => {
    const original = parseGlb(buffer);
    expect(Buffer.from(bin!).equals(Buffer.from(original.bin!))).toBe(true);
    expect(parseGlb(encodeGlb(original)).json).toEqual(original.json);
  });

  it('una cámara por vista con FOV, aspecto y ortográfica', () => {
    expect(json.cameras).toHaveLength(2);
    const [persp, ortho] = json.cameras! as Record<string, Record<string, number>>[];
    expect(persp!['perspective']!['yfov']).toBeCloseTo((50 * Math.PI) / 180);
    expect(persp!['perspective']!['aspectRatio']).toBeCloseTo(16 / 9);
    expect(ortho!['orthographic']!['ymag']).toBe(60);
    expect(ortho!['orthographic']!['xmag']).toBe(60);
  });

  it('luces de la vista elegida; las de área se omiten y se informan', () => {
    const lights = (json.extensions![LIGHTS_EXTENSION] as { lights: Record<string, unknown>[] })
      .lights;
    expect(lights.map((l) => l['type'])).toEqual(['directional', 'spot']);
    expect(result.skipped).toHaveLength(1);
    expect(json.extensionsUsed).toContain(LIGHTS_EXTENSION);
    expect(json.extensionsUsed).toContain('EXT_mesh_gpu_instancing'); // las del original siguen
  });

  it('el nodo del sol apunta en sentido contrario al vector hacia el sol', () => {
    const node = json.nodes!.find((n) => n['name'] === 'Luz: Sol')!;
    const sun = all[0]!.sun;
    const toSun = sunDirection(sun, sun.northRotationDeg);
    expect(forward(node['rotation'] as number[]).dot(toSun)).toBeCloseTo(-1, 6);
  });

  it('nodos nuevos bajo un grupo en la escena', () => {
    const scene = json.scenes![0]!.nodes!;
    const group = json.nodes![scene[scene.length - 1]!]!;
    expect(group['name']).toBe('Extrude: vistas y luces');
    expect(group['children']).toHaveLength(2 + 2);
  });

  it('pasa gltf-validator (si tools/ está instalado)', () => {
    const validator = join(REPO, 'tools/validate.mjs');
    if (!existsSync(join(REPO, 'tools/node_modules/gltf-validator'))) {
      console.warn('gltf-validator no instalado (just setup-tools): validación omitida');
      return;
    }
    const dir = mkdtempSync(join(tmpdir(), 'extrude-glb-'));
    const path = join(dir, 'with_lights.glb');
    writeFileSync(path, Buffer.from(result.glb));
    const out = spawnSync(process.execPath, [validator, path], { encoding: 'utf-8' });
    const [report] = JSON.parse(out.stdout) as {
      errors: number;
      warnings: number;
      messages: string[];
    }[];
    expect(report!.errors, report!.messages.join('\n')).toBe(0);
    expect(report!.warnings, report!.messages.join('\n')).toBe(0);
  });
});

describe('datos para Blender', () => {
  const [persp, plan] = views().map(toBlenderView);

  it('ejes, lente y resolución', () => {
    expect(persp!.camera.position).toEqual([80, -80, 60]); // (x, −z, y)
    expect(persp!.camera.lens_mm).toBeCloseTo(12 / Math.tan((25 * Math.PI) / 180));
    expect(lensFromFov(90)).toBeCloseTo(12);
    expect(plan!.camera.type).toBe('ORTHO');
    expect(plan!.camera.ortho_scale).toBe(120);
    expect(plan!.render).toEqual({ width: 2048, height: 2048 });
  });

  it('potencias: proporciones de Three conservadas', () => {
    const [spot, area] = persp!.lights;
    expect(spot!.power_w).toBeCloseTo(2000 * 4 * Math.PI);
    expect(spot!.spot_size_deg).toBe(60);
    expect(area!.power_w).toBeCloseTo(10 * 4 * 2 * Math.PI);
    expect(persp!.sun.strength).toBeCloseTo(3);
  });

  it('el sol en Blender coincide con azimut/elevación (Z arriba, norte +Y)', () => {
    const [x, y, z] = plan!.sun.to_sun;
    expect((Math.atan2(x, y) * 180) / Math.PI + 360).toBeCloseTo(285);
    expect((Math.asin(z) * 180) / Math.PI).toBeCloseTo(25);
  });

  it('capas ocultas, tone mapping y exposición', () => {
    expect(plan!.hidden_layers).toEqual(['VEGETACION']);
    expect(plan!.view_transform).toBe('Khronos PBR Neutral');
    expect(plan!.exposure_stops).toBeCloseTo(1);
  });
});

function findPython(): string | null {
  const candidates = [
    process.env['EXTRUDE_PYTHON'],
    join(REPO, 'pipeline/.venv/Scripts/python.exe'),
    join(REPO, 'pipeline/.venv/bin/python'),
  ];
  return candidates.find((p): p is string => !!p && existsSync(p)) ?? null;
}

function findBlender(): string | null {
  const env = process.env['EXTRUDE_BLENDER_PATH'];
  if (env && existsSync(env)) return env;
  const base = 'C:/Program Files/Blender Foundation';
  if (existsSync(base)) {
    for (const dir of readdirSync(base).sort().reverse()) {
      const exe = join(base, dir, 'blender.exe');
      if (existsSync(exe)) return exe;
    }
  }
  for (const exe of ['/usr/bin/blender', '/Applications/Blender.app/Contents/MacOS/Blender']) {
    if (existsSync(exe)) return exe;
  }
  return null;
}

describe('blender_setup.py', () => {
  const dir = mkdtempSync(join(tmpdir(), 'extrude-blender-'));
  const script = join(dir, 'blender_setup.py');
  writeFileSync(
    script,
    generateBlenderScript(toBlenderData(views(), MODEL, 'extrude-viewer test')),
    'utf-8',
  );
  writeFileSync(join(dir, 'model.glb'), readFileSync(SAMPLE_GLB));

  it('los nombres con comillas triples no rompen el script (datos en Base64)', () => {
    const text = readFileSync(script, 'utf-8');
    expect(text).not.toContain("'''");
    expect(text).toContain('base64.b64decode');
  });

  it('es Python sintácticamente válido (py_compile)', () => {
    const python = findPython();
    if (!python)
      throw new Error('No hay Python del pipeline (just setup-pipeline) para py_compile');
    execFileSync(python, ['-m', 'py_compile', script]);
  });

  it('en Blender (headless) crea una cámara por vista, el sol y aplica capas y renders', () => {
    const blender = findBlender();
    if (!blender) {
      console.warn('NO VERIFICADO: Blender no está instalado (EXTRUDE_BLENDER_PATH)');
      return;
    }
    const report = join(dir, 'report.json');
    const renders = join(dir, 'renders');
    const run = spawnSync(
      blender,
      [
        '-b',
        '--factory-startup',
        '-P',
        script,
        '--',
        '--report',
        report,
        '--render-all',
        renders,
        '--percentage',
        '10',
      ],
      { encoding: 'utf-8', timeout: 600_000 },
    );
    expect(run.status, `${run.stdout}\n${run.stderr}`).toBe(0);
    const data = JSON.parse(readFileSync(report, 'utf-8')) as {
      blender: string;
      cameras: {
        name: string;
        type: string;
        lens: number;
        resolution: number[];
        up: Vec3;
        forward: Vec3;
      }[];
      suns: string[];
      lights: { type: string }[];
      layers: Record<string, number>;
      active_camera: string;
      hidden_render: string[];
      view_transform: string;
      removed_defaults: string[];
    };
    console.info(`Blender ${data.blender}: ${JSON.stringify(data.layers)}`);
    expect(data.cameras.map((c) => c.name)).toEqual([
      "Vista: Perspectiva ''' con comillas",
      'Vista: Planta',
    ]);
    expect(data.cameras.map((c) => c.type)).toEqual(['PERSP', 'ORTHO']);
    expect(data.cameras[0]!.lens).toBeCloseTo(lensFromFov(50), 3);
    expect(data.cameras[1]!.resolution).toEqual([2048, 2048]);
    // Mismo encuadre que el visor, incluido el giro en la planta casi vertical
    views().forEach((view, i) => {
      // Verdad de referencia: una cámara real de Three.js, como la del visor
      const camera = new PerspectiveCamera();
      camera.position.set(...view.camera.position);
      camera.up.set(...view.camera.up);
      camera.lookAt(...view.camera.target);
      const q = camera.quaternion;
      const up = sceneToBlender(new Vector3(0, 1, 0).applyQuaternion(q).toArray());
      const fwd = sceneToBlender(new Vector3(0, 0, -1).applyQuaternion(q).toArray());
      data.cameras[i]!.up.forEach((v, k) => expect(v).toBeCloseTo(up[k]!, 4));
      data.cameras[i]!.forward.forEach((v, k) => expect(v).toBeCloseTo(fwd[k]!, 4));
    });
    expect(data.suns).toHaveLength(2);
    // La escena de fábrica (cubo, cámara y luz) se elimina: solo quedan las luces de las vistas
    expect(data.lights.map((l) => l.type).sort()).toEqual(['AREA', 'SPOT', 'SUN', 'SUN']);
    expect(data.removed_defaults).toEqual(['Camera', 'Cube', 'Light']);
    expect(Object.keys(data.layers).sort()).toEqual(['EDIFICIOS', 'MOBILIARIO', 'VEGETACION']);
    // Tras render_all queda activa la última vista (Planta): VEGETACION oculta
    expect(data.active_camera).toBe('Vista: Planta');
    expect(data.hidden_render).toEqual(expect.arrayContaining(['VEGETACION']));
    expect(data.hidden_render).not.toContain('EDIFICIOS');
    expect(data.view_transform).toBe('Khronos PBR Neutral');
    expect(readdirSync(renders).sort()).toEqual([
      '01_Perspectiva_____con_comillas.png',
      '02_Planta.png',
    ]);
  }, 600_000);
});
