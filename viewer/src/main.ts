import { App } from './app';
import {
  CONFIG_EXTENSIONS,
  ConversionClient,
  ConversionError,
  DEFAULT_SERVER,
  DRAWING_EXTENSIONS,
  hasExtension,
} from './convert/client';
import type { LoadedModel } from './scene/loader';
import { ConversionBar } from './ui/conversion';
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

const MODEL_EXTENSIONS = ['.glb'];

const container = required<HTMLElement>('#app');
const status = required<HTMLElement>('#status');
const fileInput = required<HTMLInputElement>('#file');

const app = new App(container);
new ControlPanel(app);
const views = new ViewManager(app);
new ViewsPanel(app, views, new FrameOverlay(container));

const params = new URLSearchParams(window.location.search);
const configuredServer = import.meta.env['VITE_EXTRUDE_SERVER'] as string | undefined;
const client = new ConversionClient(params.get('server') ?? configuredServer ?? DEFAULT_SERVER);
const conversion = new ConversionBar(client);
required<HTMLElement>('.toolbar').append(conversion.element);

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

async function convertAndLoad(file: File): Promise<void> {
  const base = file.name.replace(/\.(dwg|dxf)$/i, '');
  status.textContent = `Convirtiendo ${file.name}… (puede tardar con planos grandes)`;
  const result = await client.convert(file, conversion.configYaml);
  if (!result.files.glb) {
    throw new ConversionError('La conversión no generó un GLB único.', 'Convierte sin teselas.');
  }
  const model = await app.load(client.url(result.files.glb), `${base}.glb`);
  conversion.showResult(result, base);
  const { triangles } = result.report.output;
  const warnings = result.report.warnings.length;
  status.textContent =
    `${describe(model)} · ${triangles.toLocaleString('es-ES')} tri` +
    (result.config_generated ? ' · config automática (revísala)' : '') +
    (warnings ? ` · ${warnings} avisos en el informe` : '');
}

async function load(source: string | File): Promise<void> {
  try {
    if (source instanceof File && hasExtension(source.name, CONFIG_EXTENSIONS)) {
      await conversion.setConfig(source);
      status.textContent = `Configuración ${source.name} lista: arrastra ahora el .dwg o .dxf.`;
      return;
    }
    if (source instanceof File && hasExtension(source.name, DRAWING_EXTENSIONS)) {
      await convertAndLoad(source);
      return;
    }
    if (source instanceof File && !hasExtension(source.name, MODEL_EXTENSIONS)) {
      throw new ConversionError(
        `Formato no admitido: '${source.name}'.`,
        'Usa un plano .dwg o .dxf (o un .glb ya convertido, o un .yaml de configuración).',
      );
    }
    status.textContent = 'Cargando…';
    status.textContent = describe(await app.load(source));
  } catch (error) {
    // Errores de usuario esperados: solo en la barra de estado; inesperados también en consola
    if (!(error instanceof ConversionError)) console.error(error);
    status.textContent =
      error instanceof ConversionError
        ? `${error.message} ${error.fix}`
        : `No se pudo cargar: ${String(error)}`;
  }
}

bindFileInputs(container, fileInput, (file) => void load(file));
const modelParam = params.get('model');
if (modelParam) void load(modelParam);
