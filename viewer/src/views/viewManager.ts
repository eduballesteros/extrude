import type { App } from '../app';
import { generateBlenderScript, toBlenderData } from '../export/blenderScript';
import { download, downloadText } from '../export/download';
import { addCamerasAndLights } from '../export/gltfWithLights';
import { renderPng, thumbnailFromCanvas } from '../export/png';
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

export const GENERATOR = 'extrude-viewer 0.1.0';

export interface RenderPreset {
  label: string;
  width: number;
  height: number;
}

export const RENDER_PRESETS: RenderPreset[] = [
  { label: 'Full HD 16:9', width: 1920, height: 1080 },
  { label: '4K UHD 16:9', width: 3840, height: 2160 },
  { label: 'Panorámica 21:9', width: 3440, height: 1440 },
  { label: 'A3 apaisado (√2)', width: 4961, height: 3508 },
  { label: 'Cuadrado', width: 2048, height: 2048 },
  { label: 'Vertical 4:5', width: 1080, height: 1350 },
];

type Listener = () => void;

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
  private readonly listeners = new Set<Listener>();

  constructor(private readonly app: App) {
    app.on('model', () => void this.onModel());
  }

  on(listener: Listener): void {
    this.listeners.add(listener);
  }

  private changed(persist = true): void {
    for (const listener of this.listeners) listener();
    if (persist && this.modelKey && !this.loading) {
      saveViews(this.modelKey, this.views).catch((error: unknown) => {
        console.warn(error);
        this.app.notify('No se pudieron guardar las vistas en el navegador: exporta views.json.');
      });
    }
  }

  get aspect(): number {
    return this.render.width / this.render.height;
  }

  get model(): ModelInfo | null {
    const model = this.app.model;
    if (!model) return null;
    return {
      file: model.name,
      originOffset: model.extras?.origin_offset ?? [0, 0, 0],
      crs: model.extras?.crs ?? null,
    };
  }

  private async onModel(): Promise<void> {
    const token = ++this.loadToken;
    const key = this.app.model?.name ?? null;
    this.modelKey = key;
    this.views = [];
    this.activeId = null;
    this.loading = key !== null;
    if (!key) {
      this.changed(false);
      return;
    }
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
    this.loading = false;
    const createdWhileLoading = this.views; // p. ej. N pulsada antes de que llegaran las guardadas
    this.views = [...stored, ...createdWhileLoading];
    this.changed(createdWhileLoading.length > 0);
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
    const thumbnail = thumbnailFromCanvas(
      this.app.renderer,
      this.app.scene,
      this.app.nav.camera,
      this.aspect,
    );
    const view = createView(
      state,
      name ?? `Vista ${this.views.length + 1}`,
      this.render,
      thumbnail,
    );
    this.views = [...this.views, view];
    this.activeId = view.id;
    this.changed();
    return view;
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
    const { width, height } = this.render;
    const app = this.app;
    // La regla es una ayuda de pantalla (su etiqueta es HTML): no sale en el render
    const measureVisible = app.measure.group.visible;
    app.measure.group.visible = false;
    let blob: Blob;
    try {
      blob = await renderPng(app.renderer, app.scene, app.nav.camera, width, height, () =>
        app.prepareFrame(),
      );
    } finally {
      app.measure.group.visible = measureVisible;
    }
    const active = this.views.find((v) => v.id === this.activeId);
    download(blob, `${active?.name ?? 'render'}_${width}x${height}.png`);
  }
}
