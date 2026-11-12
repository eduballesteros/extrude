import './styles.css';
import { installSessionFetch, onAuthRequired, sessionInfo } from './auth/session';
import { LoginDialog } from './ui/login';
import { App } from './app';
import {
  CONFIG_EXTENSIONS,
  ConversionClient,
  ConversionCancelledError,
  ConversionError,
  ConversionTimeoutError,
  convertedModel,
  DEFAULT_TIMEOUT_MS,
  DRAWING_EXTENSIONS,
  hasExtension,
} from './convert/client';
import { ConversionSession } from './convert/session';
import { ConversionDestination } from './convert/destination';
import { type LoadedModel, ModelDownloadError } from './scene/loader';
import { FrameOverlay } from './ui/frameOverlay';
import { Inspector } from './ui/inspector';
import { MaterialsPanel } from './ui/materialsPanel';
import { LayersPanel } from './ui/layersPanel';
import { QualitySummary } from './ui/qualitySummary';
import { Sidebar } from './ui/sidebar';
import { Toasts } from './ui/toast';
import { TopBar } from './ui/topbar';
import { ViewCubeWidget } from './ui/viewCubeWidget';
import { ViewsPanel } from './ui/viewsPanel';
import { type ProgressStage, WelcomeScreen } from './ui/welcome';
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
const destination = new ConversionDestination(params.get('server'), configuredServer);
const client = new ConversionClient(destination.server, () => destination.assertUploadAllowed());
// Servidor compartido: la sesión acompaña a toda petición al servidor (también las de three.js)
installSessionFetch(destination.server);
const loginDialog = new LoginDialog(destination.server);
onAuthRequired(() => {
  void loginDialog.ask().then((user) => app.notify(`Sesión iniciada como ${user}.`, 'info'));
});
void sessionInfo(destination.server)
  .then((info) => {
    if (info.mode === 'shared' && !info.user) return loginDialog.ask();
    return null;
  })
  .catch(() => undefined); // servidor apagado: el visor sigue funcionando sin él
let configYaml: { name: string; text: string } | null = null;
/** ?tiles=500: los planos se convierten en teselas de 500 m (tiles/ + manifest.json). */
const tileSize = Number(params.get('tiles')) || undefined;

// --- Espacio de trabajo -----------------------------------------------------------

const frame = new FrameOverlay(viewport);
const topbar = new TopBar(app, views, {
  onClose: () => closeModel(),
  onToggleZen: () => toggleZen(),
});
const viewsPanel = new ViewsPanel(app, views, frame);
const layersPanel = new LayersPanel(app);
const materialsPanel = new MaterialsPanel(app, {
  server: destination.server,
  views,
  authorizeUpload: () => destination.assertUploadAllowed(),
});
const left = new Sidebar('left', [
  { id: 'views', label: 'Vistas', panel: viewsPanel.element },
  { id: 'layers', label: 'Capas', panel: layersPanel.element },
  { id: 'materials', label: 'Materiales', panel: materialsPanel.element },
]);
const syncCounts = (): void => {
  left.setCount('views', viewsPanel.count);
  left.setCount('layers', layersPanel.count);
  left.setCount('materials', materialsPanel.count);
};
views.on(syncCounts);
app.on('model', syncCounts);
syncCounts();
new Sidebar(
  'right',
  [{ id: 'scene', label: 'Inspector', panel: new Inspector(app).element }],
  'Inspector',
);
const cube = new ViewCubeWidget(app);
const quality = new QualitySummary();
viewport.append(cube.element, app.stats.element, quality.element);
app.on('model', () => quality.clear());

function toggleZen(force?: boolean): void {
  document.body.classList.toggle('is-zen', force);
}

// --- Pantalla de inicio y carga ---------------------------------------------------

let activeLoad: AbortController | null = null;
let retryOpen: (() => Promise<void>) | null = null;
const welcome = new WelcomeScreen(
  {
    onFile: (file) => void open(file),
    onSample: (sample) => void open(sample.url, `${sample.title}.glb`.replace(/\s+/g, '_'), true),
    onClose: () => app.hasModel && welcome.close(),
    onCancel: () => activeLoad?.abort(new ConversionCancelledError()),
    onRetry: () => {
      void retryOpen?.();
    },
  },
  destination,
);

function closeModel(): void {
  if (welcome.isBusy) return;
  app.unload();
  topbar.setDownloads([]);
  toggleZen(false);
  document.body.classList.remove('has-model');
  welcome.open(false);
}

function enterWorkspace(model: LoadedModel): void {
  topbar.setModel(model);
  document.body.classList.add('has-model');
  // En pantallas estrechas los paneles se superponen: empezar con ellos ocultos (Tab los muestra)
  if (window.matchMedia('(max-width: 820px)').matches) toggleZen(true);
  welcome.close();
}

function progress(stage: ProgressStage, fraction: number | null): void {
  welcome.setProgress(stage, fraction);
}

async function convertDrawing(
  session: ConversionSession,
  signal: AbortSignal,
): Promise<LoadedModel> {
  progress('convert', null);
  const result = await session.run(client, {
    signal,
    onProgress: (phase, fraction) => progress(`convert:${phase}`, fraction),
  });
  const converted = convertedModel(result);
  if (!converted) {
    throw new ConversionError('La conversión no generó ningún modelo.', 'Revisa el informe.');
  }
  const base = session.file.name.replace(/\.(dwg|dxf)$/i, '');
  const tiled = converted.kind === 'tiles';
  const href = client.url(converted.url);
  const model = await app.load(href, tiled ? base : `${base}.glb`, progress, signal);
  // Teselado: se edita el maestro sin comprimir; cada revisión regenera teselas y manifest
  const editable = tiled ? `/api/jobs/${result.job}/model.master.glb` : converted.url;
  materialsPanel.setJob(result.job, editable);
  const exportUrl = (method: string): string =>
    client.url(`/api/jobs/${result.job}/export/${method}`);
  topbar.setDownloads([
    tiled
      ? { label: 'Manifest de teselas', href, filename: `${base}_manifest.json` }
      : { label: 'GLB convertido', href, filename: `${base}.glb` },
    {
      label: 'GLB actual comprimido (Meshopt)',
      href: exportUrl('meshopt'),
      filename: `${base}.meshopt.glb`,
    },
    {
      label: 'GLB actual comprimido (Draco)',
      href: exportUrl('draco'),
      filename: `${base}.draco.glb`,
    },
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
  quality.show(result, (path) => client.url(path));
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

async function open(
  source: string | File,
  name?: string,
  sample = false,
  recovery?: ConversionSession,
): Promise<void> {
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
  if (source instanceof File && hasExtension(source.name, DRAWING_EXTENSIONS)) {
    try {
      destination.assertUploadAllowed();
    } catch (error) {
      welcome.showError(
        error instanceof ConversionError ? `${error.message} ${error.fix}` : String(error),
      );
      return;
    }
  }
  const session =
    recovery ??
    (source instanceof File && hasExtension(source.name, DRAWING_EXTENSIONS)
      ? new ConversionSession(source, configYaml?.text ?? null, tileSize)
      : null);
  const controller = new AbortController();
  activeLoad = controller;
  const timer = setTimeout(
    () => controller.abort(new ConversionTimeoutError()),
    DEFAULT_TIMEOUT_MS,
  );
  retryOpen = () => open(source, name, sample, session ?? undefined);
  welcome.startProgress(session !== null);
  try {
    const model = session
      ? await convertDrawing(session, controller.signal)
      : await app.load(source, name, progress, controller.signal);
    if (!(source instanceof File && hasExtension(source.name, DRAWING_EXTENSIONS)))
      topbar.setDownloads([]);
    enterWorkspace(model);
    retryOpen = null;
    app.notify(`${model.name} cargado`, 'success');
  } catch (caught) {
    const error: unknown = controller.signal.aborted ? controller.signal.reason : caught;
    if (!(error instanceof ConversionError)) console.error(error);
    const message = error instanceof Error ? error.message : String(error);
    welcome.showError(
      error instanceof ConversionError
        ? `${error.message} ${error.fix}`
        : sample && message.includes('HTTP 404')
          ? `Ejemplo no encontrado (${typeof source === 'string' ? source : source.name}). Genera los GLB con "just convert-samples" y cópialos a viewer/public/models/.`
          : `No se pudo cargar el modelo: ${message}`,
    );
    if (session) {
      if (error instanceof ModelDownloadError && error.status === 404) {
        session.discardExpiredResult();
      }
      // Se conserva el identificador aceptado o el resultado ya generado para recuperarlo.
      const message = error instanceof Error ? error.message : String(error);
      const fix =
        error instanceof ConversionError && !(error instanceof ConversionCancelledError)
          ? error.fix
          : '';
      welcome.showError(`${message} ${fix} ${session.recoveryHint}`);
    }
    welcome.setRetry(session?.retryLabel ?? 'Reintentar carga');
  } finally {
    clearTimeout(timer);
    activeLoad = null;
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
  if (e.key === 'Escape' && app.measure.active) {
    // Primero borra la medición; con nada medido, sale de la herramienta
    if (app.measure.pointCount) app.measure.clear();
    else app.setMeasuring(false);
    return;
  }
  switch (e.key.toLowerCase()) {
    case 'm':
      app.setMeasuring(!app.measure.active);
      break;
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
    case '1':
    case '2':
    case '3':
    case '4':
    case '5':
    case '6':
    case '7':
    case '8':
    case '9': {
      const view = views.views[Number(e.key) - 1];
      if (view) views.restore(view.id);
      break;
    }
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
