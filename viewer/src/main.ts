import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { boundsOf, frameBox } from './scene/framing';
import { type LoadedModel, loadFromFile, loadFromUrl } from './scene/loader';
import { bindFileInputs } from './ui/dropzone';
import { StatsOverlay } from './ui/stats';

function required<T extends HTMLElement>(selector: string): T {
  const el = document.querySelector<T>(selector);
  if (!el) throw new Error(`Falta el elemento ${selector} en index.html`);
  return el;
}

const container = required<HTMLElement>('#app');
const status = required<HTMLElement>('#status');
const fileInput = required<HTMLInputElement>('#file');

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(container.clientWidth, container.clientHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xbfd1e5);
scene.add(new THREE.HemisphereLight(0xffffff, 0x8d8d7a, 1.2));
const sun = new THREE.DirectionalLight(0xffffff, 2.0);
sun.position.set(1, 2, 1.5);
scene.add(sun);

const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 5000);
camera.position.set(80, 60, 80);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

const stats = new StatsOverlay(renderer);
document.body.appendChild(stats.element);

let current: LoadedModel | null = null;

function show(model: LoadedModel): void {
  if (current) scene.remove(current.root);
  current = model;
  scene.add(model.root);
  frameBox(camera, controls, boundsOf(model.root));
  const offset = model.extras?.origin_offset;
  status.textContent = offset
    ? `${model.name} · origen ${offset.map((v) => v.toFixed(2)).join(', ')} m`
    : model.name;
}

async function load(source: string | File): Promise<void> {
  status.textContent = 'Cargando…';
  try {
    show(typeof source === 'string' ? await loadFromUrl(source) : await loadFromFile(source));
  } catch (error) {
    status.textContent = `No se pudo cargar el modelo: ${String(error)}`;
    console.error(error);
  }
}

function resize(): void {
  camera.aspect = container.clientWidth / container.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(container.clientWidth, container.clientHeight);
}

window.addEventListener('resize', resize);
resize();
bindFileInputs(container, fileInput, (file) => void load(file));

const modelParam = new URLSearchParams(window.location.search).get('model');
if (modelParam) void load(modelParam);

renderer.setAnimationLoop((now: number) => {
  controls.update();
  renderer.render(scene, camera);
  stats.update(now);
});
