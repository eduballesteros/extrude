import './styles.css';
import { App } from './app';
import {
  CONFIG_EXTENSIONS,
  ConversionClient,
  ConversionError,
  DEFAULT_SERVER,
  DRAWING_EXTENSIONS,
  hasExtension,
} from './convert/client';
import type { LoadedModel, LoadStage } from './scene/loader';
import { FrameOverlay } from './ui/frameOverlay';
import { Inspector } from './ui/inspector';
import { LayersPanel } from './ui/layersPanel';
import { Sidebar } from './ui/sidebar';
import { Toasts } from './ui/toast';
import { TopBar } from './ui/topbar';
import { ViewCubeWidget } from './ui/viewCubeWidget';
import { ViewsPanel } from './ui/viewsPanel';
import { WelcomeScreen } from './ui/welcome';
import { ViewManager } from './views/viewManager';

const MODEL_EXTENSIONS = ['.glb', '.gltf'];

function required<T extends HTMLElement>(selector: string): T {
  const el = document.querySelector<T>(selector);
  if (!el) throw new Error(`Falta el elemento ${selector} en index.html`);
  return el;
}

const viewport = required<HTMLElement>('#viewport');
const app = new App(viewport);
const views = new ViewManager(app);
new Toasts();

const params = new URLSearchParams(window.location.search);
const configuredServer = import.meta.env['VITE_EXTRUDE_SERVER'] as string | undefined;
const client = new ConversionClient(params.get('server') ?? configuredServer ?? DEFAULT_SERVER);
let configYaml: { name: string; text: string } | null = null;

// --- Espacio de trabajo -----------------------------------------------------------

const frame = new FrameOverlay(viewport);
const topbar = new TopBar(app, views, {
  onOpen: () => welcome.open(app.hasModel),
  onToggleZen: () => toggleZen(),
});
new Sidebar('left', [
  { id: 'views', label: 'Vistas', icon: 'views', panel: new ViewsPanel(app, views, frame).element },
  { id: 'layers', label: 'Capas', icon: 'layers', panel: new LayersPanel(app).element },
]);
new Sidebar('right', [
  { id: 'scene', label: 'Escena', icon: 'settings', panel: new Inspector(app).element },
]);
const cube = new ViewCubeWidget(app);
viewport.append(cube.element, app.stats.element);

function toggleZen(force?: boolean): void {
  document.body.classList.toggle('is-zen', force);
}

// --- Pantalla de inicio y carga ---------------------------------------------------

const welcome = new WelcomeScreen({
  onFile: (file) => void open(file),
  onSample: (sample) => void open(sample.url, `${sample.title}.glb`.replace(/\s+/g, '_')),
  onClose: () => app.hasModel && welcome.close(),
});

function enterWorkspace(model: LoadedModel): void {
  topbar.setModel(model);
  document.body.classList.add('has-model');
  welcome.close();
}

function progress(stage: LoadStage | 'convert', fraction: number | null): void {
  welcome.setProgress(stage, fraction);
}

async function convertDrawing(file: File): Promise<LoadedModel> {
  progress('convert', null);
  const result = await client.convert(file, configYaml?.text ?? null);
  if (!result.files.glb) {
    throw new ConversionError('La conversión no generó un GLB único.', 'Convierte sin teselas.');
  }
  const base = file.name.replace(/\.(dwg|dxf)$/i, '');
  const model = await app.load(client.url(result.files.glb), `${base}.glb`, progress);
  topbar.setDownloads([
    { label: 'GLB convertido', href: client.url(result.files.glb), filename: `${base}.glb` },
    {
      label: 'Informe de conversión',
      href: client.url(result.files.report_md),
      filename: `${base}_report.md`,
    },
    {
      label: result.config_generated ? 'Configuración generada' : 'Configuración usada',
      href: client.url(result.files.config),
      filename: `${base}.yaml`,
    },
  ]);
  const warnings = result.report.warnings.length;
  if (result.config_generated) {
    app.notify(
      'Convertido con configuración automática: revísala en Exportar → Resultados.',
      'warning',
    );
  } else if (warnings) {
    app.notify(`Convertido con ${warnings} avisos: consulta el informe.`, 'warning');
  }
  return model;
}

async function open(source: string | File, name?: string): Promise<void> {
  if (welcome.isBusy) return;
  if (source instanceof File && hasExtension(source.name, CONFIG_EXTENSIONS)) {
    configYaml = { name: source.name, text: await source.text() };
    welcome.setConfig(source.name);
    if (!welcome.visible) welcome.open(app.hasModel);
    app.notify(`Configuración ${source.name} lista: suelta ahora el .dwg o .dxf.`, 'success');
    return;
  }
  if (
    source instanceof File &&
    !hasExtension(source.name, [...DRAWING_EXTENSIONS, ...MODEL_EXTENSIONS])
  ) {
    welcome.open(app.hasModel);
    welcome.showError(
      `Formato no admitido: "${source.name}". Usa .dwg, .dxf, .glb o .gltf (y .yaml de configuración).`,
    );
    return;
  }
  welcome.open(app.hasModel);
  welcome.startProgress();
  try {
    const model =
      source instanceof File && hasExtension(source.name, DRAWING_EXTENSIONS)
        ? await convertDrawing(source)
        : await app.load(source, name, progress);
    if (!(source instanceof File && hasExtension(source.name, DRAWING_EXTENSIONS)))
      topbar.setDownloads([]);
    enterWorkspace(model);
    app.notify(`${model.name} cargado`, 'success');
  } catch (error) {
    if (!(error instanceof ConversionError)) console.error(error);
    welcome.showError(
      error instanceof ConversionError
        ? `${error.message} ${error.fix}`
        : `No se pudo cargar el modelo: ${error instanceof Error ? error.message : String(error)}`,
    );
  } finally {
    welcome.endProgress();
  }
}

// --- Arrastrar y soltar en cualquier momento ----------------------------------------

let dragDepth = 0;
window.addEventListener('dragenter', (e) => {
  e.preventDefault();
  dragDepth += 1;
  welcome.setDragging(true);
  document.body.classList.add('is-dragging');
});
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('dragleave', () => {
  dragDepth = Math.max(dragDepth - 1, 0);
  if (dragDepth === 0) {
    welcome.setDragging(false);
    document.body.classList.remove('is-dragging');
  }
});
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  welcome.setDragging(false);
  document.body.classList.remove('is-dragging');
  const file = e.dataTransfer?.files[0];
  if (file) void open(file);
});

// --- Atajos de teclado ---------------------------------------------------------------

window.addEventListener('keydown', (e) => {
  const target = e.target as HTMLElement | null;
  if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return;
  if (e.key === 'Escape' && welcome.visible && app.hasModel && !welcome.isBusy) {
    welcome.close();
    return;
  }
  if (!app.hasModel || welcome.visible || app.nav.flying || e.ctrlKey || e.metaKey || e.altKey)
    return;
  switch (e.key.toLowerCase()) {
    case 'f':
      app.nav.frame();
      break;
    case 'n':
      try {
        views.create();
        app.notify('Vista guardada', 'success');
      } catch (error) {
        app.notify(String(error instanceof Error ? error.message : error), 'error');
      }
      break;
    case 'tab':
      e.preventDefault();
      toggleZen();
      break;
    case ' ':
      if (app.state.sun.mode === 'datetime') {
        e.preventDefault();
        app.setPlaying(!app.animation.playing);
      }
      break;
  }
});

// ?debug expone la app para inspeccionarla desde la consola o automatizar pruebas
if (params.has('debug')) {
  Object.assign(window as unknown as Record<string, unknown>, { __extrude: app, __views: views });
}

const modelParam = params.get('model');
if (modelParam) void open(modelParam);
