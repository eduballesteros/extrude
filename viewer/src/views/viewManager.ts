import type { App } from '../app';
import type { ViewerState } from '../state';
import { generateBlenderScript, toBlenderData } from '../export/blenderScript';
import { download, downloadText } from '../export/download';
import { addCamerasAndLights } from '../export/gltfWithLights';
import { renderPng, thumbnailFromCanvas } from '../export/png';
import { ProjectLock } from './projectLock';
import { loadViews, saveViews } from './storage';
import {
  createView,
  duplicateView,
  frameHeightFraction,
  type ModelInfo,
  moveView,
  parseViewsFile,
  renderFov,
  type SceneView,
  toViewsFile,
  viewerFov,
  viewToState,
} from './viewState';

/** Versión del visor (package.json, inyectada por Vite) en views.json y en el script de Blender. */
export const GENERATOR = `extrude-viewer ${(import.meta.env['VITE_APP_VERSION'] as string | undefined) ?? 'dev'}`;

type Listener = () => void;

const READ_ONLY_NOTICE =
  'Este proyecto ya está abierto en otra ventana de Extrude. Aquí las vistas no se guardarán, para no borrar las de la otra: cierra una de las dos.';
const READ_ONLY_REMINDER =
  'No se ha guardado: este proyecto está abierto en otra ventana. Ciérrala para guardar aquí.';
const REMINDER_INTERVAL_MS = 15_000;

/**
 * «Vista N» con el siguiente número libre. Contar las vistas repetía nombres: con «Vista 1» y
 * «Vista 2», borrar la 1 hacía que la siguiente volviera a llamarse «Vista 2».
 */
export function nextViewName(views: readonly { name: string }[]): string {
  const used = views
    .map((view) => /^Vista (\d+)$/.exec(view.name.trim())?.[1])
    .filter((n): n is string => n !== undefined)
    .map(Number);
  return `Vista ${Math.max(0, ...used) + 1}`;
}

/** Lista de vistas del modelo cargado: CRUD, persistencia y exportaciones. */
export class ViewManager {
  views: SceneView[] = [];
  activeId: string | null = null;
  render = { width: 1920, height: 1080 };
  showFrame = true;
  private modelKey: string | null = null;
  /** Cada cambio de modelo invalida las cargas anteriores aún pendientes. */
  private loadToken = 0;
  /** Mientras llegan las vistas guardadas no se persiste: pisaría las del almacenamiento. */
  private loading = false;
  /** Otra ventana tiene abierto este proyecto: aquí no se guarda (ver projectLock.ts). */
  private readOnly = false;
  private lastReminder = 0;
  private readonly listeners = new Set<Listener>();

  constructor(
    private readonly app: App,
    private readonly lock = new ProjectLock(),
  ) {
    app.on('model', () => void this.onModel());
  }

  /** Esta ventana no guarda las vistas porque otra tiene abierto el mismo proyecto. */
  get isReadOnly(): boolean {
    return this.readOnly;
  }

  on(listener: Listener): void {
    this.listeners.add(listener);
  }

  private changed(persist = true): void {
    for (const listener of this.listeners) listener();
    if (persist && this.modelKey && !this.loading) {
      if (this.readOnly) {
        this.remindReadOnly();
        return;
      }
      saveViews(this.modelKey, this.views).catch((error: unknown) => {
        console.warn(error);
        this.app.notify('No se pudieron guardar las vistas en el navegador: exporta views.json.');
      });
    }
  }

  private remindReadOnly(): void {
    const now = Date.now();
    if (now - this.lastReminder < REMINDER_INTERVAL_MS) return;
    this.lastReminder = now;
    this.app.notify(READ_ONLY_REMINDER, 'warning');
  }

  get aspect(): number {
    return this.render.width / this.render.height;
  }

  get model(): ModelInfo | null {
    const model = this.app.model;
    if (!model) return null;
    // Con materiales confirmados, Blender y views.json apuntan al GLB materializado que entrega
    // la pestaña Materiales (`<nombre>.materialized.glb`), no al GLB base sin materiales.
    const materialized = Boolean(model.extras?.material_revision) && !this.app.materialPreview;
    return {
      file: materialized ? model.name.replace(/(\.glb)?$/i, '.materialized.glb') : model.name,
      originOffset: model.extras?.origin_offset ?? [0, 0, 0],
      crs: model.extras?.crs ?? null,
    };
  }

  private async onModel(): Promise<void> {
    const key = this.app.model?.name ?? null;
    // Recarga del mismo modelo (previsualizar, confirmar o deshacer materiales/ediciones): las
    // vistas son del proyecto, no de la revisión del GLB; releerlas pisaría cambios recientes.
    if (key !== null && key === this.modelKey && !this.loading) return;
    const token = ++this.loadToken;
    this.modelKey = key;
    this.views = [];
    this.activeId = null;
    this.loading = key !== null;
    this.readOnly = false;
    if (!key) {
      this.lock.release();
      this.changed(false);
      return;
    }
    const owned = this.lock.claim(key, () => void this.onLockAcquired(token, key));
    let stored: SceneView[] = [];
    try {
      stored = await loadViews(key);
    } catch (error) {
      if (token !== this.loadToken) return;
      console.warn(error);
      this.app.notify('Almacenamiento local no disponible: las vistas no se guardarán al cerrar.');
    }
    // Si entretanto se abrió otro modelo (o el mismo otra vez), esta carga ya no vale: aplicarla
    // pondría sus vistas en la lista del modelo nuevo y se guardarían bajo su clave.
    if (token !== this.loadToken) return;
    this.readOnly = !(await owned);
    if (token !== this.loadToken) return;
    this.loading = false;
    if (this.readOnly) this.app.notify(READ_ONLY_NOTICE, 'warning');
    const createdWhileLoading = this.views; // p. ej. N pulsada antes de que llegaran las guardadas
    this.views = [...stored, ...createdWhileLoading];
    this.changed(createdWhileLoading.length > 0);
  }

  /**
   * La otra ventana soltó el proyecto: esta pasa a guardar. Se releen las vistas que dejó
   * guardadas y se conservan las creadas aquí mientras tanto, sin perder ninguna.
   */
  private async onLockAcquired(token: number, key: string): Promise<void> {
    if (token !== this.loadToken) return;
    let stored: SceneView[] = [];
    try {
      stored = await loadViews(key);
    } catch (error) {
      console.warn(error);
    }
    if (token !== this.loadToken) return;
    const known = new Set(stored.map((view) => view.id));
    const createdHere = this.views.filter((view) => !known.has(view.id));
    const wasReadOnly = this.readOnly;
    this.readOnly = false;
    this.views = [...stored, ...createdHere];
    if (this.activeId && !this.views.some((view) => view.id === this.activeId)) {
      this.activeId = null;
    }
    this.changed(createdHere.length > 0);
    if (wasReadOnly) {
      this.app.notify(
        'La otra ventana de este proyecto se ha cerrado: a partir de ahora las vistas se guardan aquí.',
        'success',
      );
    }
  }

  setRender(width: number, height: number): void {
    this.render = { width: Math.round(width), height: Math.round(height) };
    this.changed(false);
  }

  /** Captura la vista actual con el encuadre del marco de cámara. */
  create(name?: string): SceneView {
    const state = this.app.snapshot();
    const viewportAspect = this.app.viewportAspect;
    state.camera.fovDeg = renderFov(state.camera.fovDeg, viewportAspect, this.aspect);
    state.camera.orthoSize *= frameHeightFraction(viewportAspect, this.aspect);
    const thumbnail = this.thumbnail();
    const view = createView(state, name ?? nextViewName(this.views), this.render, thumbnail);
    this.views = [...this.views, view];
    this.activeId = view.id;
    this.changed();
    return view;
  }

  /** Miniatura del encuadre actual, sin ayudas de pantalla (regla, resaltados). */
  private thumbnail(): string {
    const restore = this.app.hideHelpers();
    try {
      return thumbnailFromCanvas(
        this.app.renderer,
        this.app.scene,
        this.app.nav.camera,
        this.aspect,
      );
    } finally {
      restore();
    }
  }

  /**
   * Renderiza una vista guardada a `width × height` (su misma proporción) y devuelve el PNG.
   * Aplica el estado de la vista solo durante el render: la cámara, el sol y las capas del
   * visor vuelven a como estaban antes de que se pinte el siguiente fotograma.
   */
  captureView(id: string, width: number, height: number): Promise<Blob> {
    this.assertExportable();
    const view = this.views.find((v) => v.id === id);
    if (!view) throw new Error('Esa vista ya no existe.');
    const app = this.app;
    const previous = app.snapshot();
    const render = this.render;
    const active = this.activeId;
    const restoreHelpers = app.hideHelpers();
    try {
      this.restore(id, false);
      return renderPng(app.renderer, app.scene, app.nav.camera, width, height, () =>
        app.prepareFrame(),
      );
    } finally {
      restoreHelpers();
      app.setState(previous, false);
      this.render = render;
      this.activeId = active;
      this.changed(false);
    }
  }

  restore(id: string, animate = true): void {
    const view = this.views.find((v) => v.id === id);
    if (!view) return;
    const state = viewToState(view);
    const viewportAspect = this.app.viewportAspect;
    const renderAspect = view.render.width / view.render.height;
    state.camera.fovDeg = viewerFov(view.camera.fovDeg, viewportAspect, renderAspect);
    state.camera.orthoSize /= frameHeightFraction(viewportAspect, renderAspect);
    this.render = { width: view.render.width, height: view.render.height };
    this.app.setState(state, animate);
    this.activeId = id;
    this.changed(false);
  }

  rename(id: string, name: string): void {
    const trimmed = name.trim();
    if (!trimmed) return;
    this.views = this.views.map((v) => (v.id === id ? { ...v, name: trimmed } : v));
    this.changed();
  }

  /** Sustituye el contenido de una vista por el estado actual (conserva id y nombre). */
  update(id: string): void {
    const index = this.views.findIndex((v) => v.id === id);
    if (index < 0) return;
    const old = this.views[index]!;
    const fresh = this.create(old.name);
    this.views = this.views.filter((v) => v.id !== fresh.id);
    this.views[index] = { ...fresh, id: old.id, createdAt: old.createdAt };
    this.activeId = old.id;
    this.changed();
  }

  duplicate(id: string): void {
    const index = this.views.findIndex((v) => v.id === id);
    if (index < 0) return;
    const copy = duplicateView(this.views[index]!);
    this.views = [...this.views.slice(0, index + 1), copy, ...this.views.slice(index + 1)];
    this.changed();
  }

  move(id: string, delta: -1 | 1): void {
    this.views = moveView(this.views, id, delta);
    this.changed();
  }

  has(id: string): boolean {
    return this.views.some((v) => v.id === id);
  }

  /**
   * Añade una vista con id, nombre y estado ya preparados (cambios con IA confirmados, o al
   * rehacerlos). La miniatura sale de la cámara actual: quien llama aplica antes ese estado.
   */
  insertPrepared(id: string, name: string, state: ViewerState): void {
    if (this.has(id)) return;
    const thumbnail = this.thumbnail();
    const view = { ...createView(state, name, this.render, thumbnail), id };
    this.views = [...this.views, view];
    this.activeId = id;
    this.changed();
  }

  remove(id: string): void {
    this.views = this.views.filter((v) => v.id !== id);
    if (this.activeId === id) this.activeId = null;
    this.changed();
  }

  // --- Exportación / importación ---------------------------------------------

  private requireModel(): ModelInfo {
    const model = this.model;
    if (!model) throw new Error('Carga primero un modelo GLB.');
    return model;
  }

  /** Las exportaciones usan la materialidad confirmada, nunca una previsualización. */
  assertExportable(): void {
    if (this.app.materialPreview) {
      throw new Error(
        'Hay una previsualización de materiales sin confirmar: confírmala o descártala antes de exportar.',
      );
    }
  }

  private exportable(): SceneView[] {
    if (this.views.length === 0) throw new Error('No hay vistas: crea al menos una.');
    return this.views;
  }

  exportViewsJson(): void {
    const file = toViewsFile(this.exportable(), this.requireModel(), GENERATOR);
    downloadText(JSON.stringify(file, null, 2), 'views.json', 'application/json');
  }

  async importViewsJson(file: File): Promise<number> {
    const { views, originOffset } = parseViewsFile(await file.text());
    const model = this.model;
    if (
      model &&
      originOffset &&
      originOffset.some((v, i) => Math.abs(v - model.originOffset[i]!) > 1e-6)
    ) {
      this.app.notify(
        'Aviso: el views.json se creó para un modelo con otro origin_offset; las cámaras pueden no encajar.',
      );
    }
    const known = new Set(this.views.map((v) => v.id));
    const incoming = views.map((v) => (known.has(v.id) ? { ...v, id: crypto.randomUUID() } : v));
    this.views = [...this.views, ...incoming];
    this.changed();
    return incoming.length;
  }

  exportBlenderScript(): void {
    this.assertExportable();
    if (this.app.model?.tiles) {
      throw new Error(
        'La exportación a Blender de modelos teselados aún no está disponible. ' +
          'Exporta views.json o vuelve a convertir sin teselas para obtener un GLB único.',
      );
    }
    const script = generateBlenderScript(
      toBlenderData(this.exportable(), this.requireModel(), GENERATOR),
    );
    downloadText(script, 'blender_setup.py', 'text/x-python');
  }

  /** GLB original + cámaras de todas las vistas + luces de la vista activa (o la primera). */
  exportGlbWithLights(): string[] {
    this.assertExportable();
    const model = this.app.model;
    if (!model) throw new Error('Carga primero un modelo GLB.');
    if (!model.bytes) {
      throw new Error('Un modelo teselado no tiene un GLB único: exporta las vistas a views.json.');
    }
    const bytes = model.bytes;
    const views = this.exportable();
    const lightsFrom = views.find((v) => v.id === this.activeId) ?? views[0]!;
    const result = addCamerasAndLights(bytes, views, lightsFrom);
    const base = model.name.replace(/\.glb$/i, '');
    download(new Blob([result.glb], { type: 'model/gltf-binary' }), `${base}_vistas.glb`);
    return result.skipped;
  }

  async exportPng(): Promise<void> {
    this.assertExportable();
    const { width, height } = this.render;
    const app = this.app;
    // La regla y los resaltados son ayudas de pantalla: no salen en el render
    const restoreHelpers = app.hideHelpers();
    let pending: Promise<Blob>;
    try {
      pending = renderPng(app.renderer, app.scene, app.nav.camera, width, height, () =>
        app.prepareFrame(),
      );
    } finally {
      restoreHelpers();
    }
    const blob = await pending;
    const active = this.views.find((v) => v.id === this.activeId);
    download(blob, `${active?.name ?? 'render'}_${width}x${height}.png`);
  }
}
