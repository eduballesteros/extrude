import { App } from './app';
import type { LoadedModel } from './scene/loader';
import { bindFileInputs } from './ui/dropzone';
import { FrameOverlay } from './ui/frameOverlay';
import { ControlPanel } from './ui/panel';
import { ViewsPanel } from './ui/viewsPanel';
import { ViewManager } from './views/viewManager';

function required<T extends HTMLElement>(selector: string): T {
  const el = document.querySelector<T>(selector);
  if (!el) throw new Error(`Falta el elemento ${selector} en index.html`);
  return el;
}

const container = required<HTMLElement>('#app');
const status = required<HTMLElement>('#status');
const fileInput = required<HTMLInputElement>('#file');

const app = new App(container);
new ControlPanel(app);
const views = new ViewManager(app);
new ViewsPanel(app, views, new FrameOverlay(container));

const params = new URLSearchParams(window.location.search);
// ?debug expone la app para inspeccionarla desde la consola o automatizar pruebas
if (params.has('debug')) {
  Object.assign(window as unknown as Record<string, unknown>, { __extrude: app, __views: views });
}

function describe(model: LoadedModel): string {
  const offset = model.extras?.origin_offset;
  const crs = model.extras?.crs ? ` · ${model.extras.crs}` : '';
  return offset
    ? `${model.name} · origen ${offset.map((v) => v.toFixed(2)).join(', ')} m${crs}`
    : model.name;
}

async function load(source: string | File): Promise<void> {
  status.textContent = 'Cargando…';
  try {
    status.textContent = describe(await app.load(source));
  } catch (error) {
    status.textContent = `No se pudo cargar el modelo: ${String(error)}`;
    console.error(error);
  }
}

bindFileInputs(container, fileInput, (file) => void load(file));
const modelParam = params.get('model');
if (modelParam) void load(modelParam);
