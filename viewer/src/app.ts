import {
  type Box3,
  Group,
  type Mesh,
  type Object3D,
  Raycaster,
  Scene,
  SRGBColorSpace,
  type Texture,
  Vector2,
  WebGLRenderer,
} from 'three';
import { disposeObject } from './scene/dispose';
import { loadHdri } from './scene/environment';
import { misplacedLayers, misplacedMessage } from './scene/groundCheck';
import { MeasureTool } from './scene/measure';
import { Ground } from './scene/ground';
import { ObjectHighlight } from './scene/highlight';
import { LayerManager } from './scene/layers';
import { SceneLighting } from './scene/lighting';
import { ExtraLights, newLightState } from './scene/lights';
import {
  isManifestUrl,
  type LoadedModel,
  loadFromFile,
  loadFromManifest,
  loadFromUrl,
  nextFrame,
  type ProgressCallback,
} from './scene/loader';
import { boundsOf, Navigation, type ViewPreset } from './scene/navigation';
import { describeHit, firstVisibleHit } from './scene/picking';
import { composeIso, geolocate, splitIso } from './scene/sun';
import { sharpenTextures } from './scene/textures';
import { defaultState, type LightType, type Vec3, type ViewerState } from './state';
import { InfoPanel } from './ui/info';
import { StatsOverlay } from './ui/stats';

/** Desplazamiento máximo del puntero (px) para considerar un clic y no un arrastre. */
const CLICK_TOLERANCE_PX = 4;
/** Altura sobre la superficie a la que se coloca una luz nueva (m). */
const LIGHT_PLACEMENT_OFFSET_M = 3;
const LINE_PICK_THRESHOLD_M = 0.5;

export interface DayAnimation {
  playing: boolean;
  hoursPerSecond: number;
}

type Listener = () => void;

export type NoticeKind = 'info' | 'success' | 'warning' | 'error';

/** Evento global de avisos (lo muestra el sistema de toasts). */
export const NOTICE_EVENT = 'extrude:notice';

export interface Notice {
  message: string;
  kind: NoticeKind;
}

export class App {
  readonly renderer: WebGLRenderer;
  readonly scene = new Scene();
  readonly state: ViewerState = defaultState();
  readonly animation: DayAnimation = { playing: false, hoursPerSecond: 1 };
  readonly nav: Navigation;
  readonly lighting: SceneLighting;
  readonly layers = new LayerManager();
  readonly lights: ExtraLights;
  readonly info: InfoPanel;
  readonly ground = new Ground();
  readonly stats: StatsOverlay;
  /** Regla 3D (tecla M): mientras está activa, los clics miden en vez de inspeccionar. */
  readonly measure: MeasureTool;
  model: LoadedModel | null = null;
  sceneBox: Box3 | null = null;
  /** Tipo de luz pendiente de colocar con el siguiente clic. */
  placing: LightType | null = null;
  /** Nombre del HDRI cargado en esta sesión (el contenido no se guarda en las vistas). */
  hdriName: string | null = null;

  private readonly listeners = {
    model: new Set<Listener>(),
    state: new Set<Listener>(),
    selection: new Set<Listener>(),
  };
  /** Objeto pulsado en la vista 3D (ID persistente), para el panel de materiales. */
  selectedObjectId: string | null = null;
  /** Cajas de los objetos afectados por una propuesta de materiales (no cambia el modelo). */
  readonly highlight = new ObjectHighlight();
  /**
   * El modelo cargado es una previsualización de materiales sin confirmar: las exportaciones
   * deben usar la revisión confirmada, así que se bloquean mientras dure.
   */
  materialPreview = false;
  private readonly raycaster = new Raycaster();
  private pointerDown = new Vector2();
  private lastFrame = performance.now();
  private shadowsDirty = true;

  constructor(private readonly container: HTMLElement) {
    // Profundidad logarítmica: terrenos de varios km con capas a pocos cm del suelo (vegetación,
    // viales drapeados) parpadean (z-fighting) con el buffer lineal de 24 bits al acercarse.
    this.renderer = new WebGLRenderer({
      antialias: true,
      preserveDrawingBuffer: false,
      logarithmicDepthBuffer: true,
    });
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.renderer.outputColorSpace = SRGBColorSpace;
    container.appendChild(this.renderer.domElement);

    this.nav = new Navigation(
      this.renderer.domElement,
      (camera) => this.lights.setCamera(camera),
      () =>
        this.notify(
          'El navegador no concedió el bloqueo del puntero: haz clic en la vista y vuelve a intentarlo.',
        ),
    );
    this.lighting = new SceneLighting(this.scene, this.renderer);
    this.lights = new ExtraLights(
      this.scene,
      this.nav.camera,
      this.renderer.domElement,
      this.nav.orbit,
      (id, position) => this.moveLight(id, position),
    );
    this.stats = new StatsOverlay(this.renderer);
    this.info = new InfoPanel((point) => this.nav.focus(point));
    this.raycaster.params.Line = { threshold: LINE_PICK_THRESHOLD_M };
    this.scene.add(this.ground.mesh);
    container.append(this.info.element);
    this.measure = new MeasureTool(container, () => this.emit('state'));
    this.scene.add(this.measure.group);
    this.scene.add(this.highlight.group);

    this.bindPointer();
    // Los paneles acoplados cambian el tamaño del visor sin redimensionar la ventana
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.applyAll();
    this.renderer.setAnimationLoop((now: number) => this.frame(now));
  }

  /** Aviso breve para el usuario (toast). */
  notify(message: string, kind: NoticeKind = 'info'): void {
    window.dispatchEvent(new CustomEvent<Notice>(NOTICE_EVENT, { detail: { message, kind } }));
  }

  get hasModel(): boolean {
    return this.model !== null;
  }

  on(event: keyof App['listeners'], listener: Listener): void {
    this.listeners[event].add(listener);
  }

  private emit(event: keyof App['listeners']): void {
    for (const listener of this.listeners[event]) listener();
  }

  /**
   * `name` identifica el modelo (vistas guardadas, script de Blender); por defecto, el archivo.
   * Una URL a `manifest.json` abre el conjunto teselado de `extrude convert --tiles`.
   */
  async load(
    source: string | File,
    name?: string,
    onProgress?: ProgressCallback,
    signal?: AbortSignal,
    options: { keepView?: boolean } = {},
  ): Promise<LoadedModel> {
    const model =
      typeof source !== 'string'
        ? await loadFromFile(source, onProgress)
        : isManifestUrl(source)
          ? await loadFromManifest(source, name, onProgress, signal)
          : await loadFromUrl(source, name, onProgress, signal);
    onProgress?.('prepare', null);
    await nextFrame();
    if (signal?.aborted) {
      disposeObject(model.root);
      signal.throwIfAborted();
    }
    this.setModel(model, options.keepView ?? false);
    return model;
  }

  /** Cierra el modelo y libera su memoria de GPU (vuelta a la pantalla de inicio). */
  unload(): void {
    if (!this.model) return;
    this.releaseModel();
    this.animation.playing = false;
    this.layers.attach(new Group());
    this.state.layers = {};
    this.info.hide();
    this.renderer.clear();
    this.emit('model');
    this.emit('state');
  }

  /** Resalta objetos por ID persistente; devuelve cuántos hay en el modelo. */
  highlightObjects(ids: Iterable<string>): number {
    return this.highlight.show(this.model?.root ?? null, ids);
  }

  /** Activa o desactiva la regla: cursor de mirilla y clics para medir. */
  setMeasuring(active: boolean): void {
    if (active) {
      this.placing = null;
      this.info.hide();
    }
    this.container.classList.toggle('is-measuring', active);
    this.measure.setActive(active);
  }

  /** Reproduce o pausa la animación del día (y avisa a la interfaz). */
  setPlaying(playing: boolean): void {
    this.animation.playing = playing;
    this.emit('state');
  }

  /** Lleva la cámara a una vista estándar (planta, alzados, isométrica). */
  viewFrom(preset: ViewPreset): void {
    this.nav.viewFrom(preset);
  }

  async loadHdri(file: File): Promise<void> {
    this.lighting.setHdri(await loadHdri(file));
    this.hdriName = file.name;
    this.state.ambient.hdri = file.name;
    this.state.ambient.background = 'hdri';
    this.applyAmbient();
    this.emit('state');
  }

  /** Quita el modelo actual de la escena y libera su memoria de GPU (sin tocar la interfaz). */
  private releaseModel(): void {
    const model = this.model;
    if (!model) return;
    this.measure.clear(); // los puntos eran del modelo anterior
    this.highlight.clear();
    this.scene.remove(model.root);
    // El entorno (cielo/HDRI) es de SceneLighting y se reutiliza con el siguiente modelo
    const keep = new Set<Texture>();
    for (const t of [this.scene.environment, this.scene.background]) {
      if ((t as Texture | null)?.isTexture) keep.add(t as Texture);
    }
    disposeObject(model.root, keep);
    this.model = null;
  }

  /** Lleva el sol al centro geográfico del modelo (si el pipeline lo calculó desde su CRS). */
  private geolocateSun(model: LoadedModel): boolean {
    const place = geolocate(this.state.sun.isoDate, model.extras);
    if (!place) return false;
    const sun = this.state.sun;
    sun.lat = place.lat;
    sun.lon = place.lon;
    sun.isoDate = place.isoDate;
    const where = `${place.lat.toFixed(4)}°, ${place.lon.toFixed(4)}°`;
    const crs = model.extras?.crs ? ` (${model.extras.crs})` : '';
    const sign = place.utcOffsetH < 0 ? '−' : '+';
    const zone = place.zoneChanged
      ? `. Zona horaria aproximada UTC${sign}${Math.abs(place.utcOffsetH)}: ajústala si hace falta`
      : '';
    this.notify(`Sol situado en el centro del plano${crs}: ${where}${zone}.`, 'info');
    return true;
  }

  /**
   * `keepView`: sustituir el modelo por otra versión del mismo (materiales previsualizados o
   * confirmados) sin mover la cámara, el sol ni la visibilidad y color de las capas.
   */
  private setModel(model: LoadedModel, keepView = false): void {
    const previousLayers = this.state.layers;
    const previousGround = this.state.ground.enabled;
    this.releaseModel();
    this.model = model;
    model.root.traverse((o: Object3D) => {
      const mesh = o as Mesh;
      if (mesh.isMesh) {
        mesh.castShadow = true;
        mesh.receiveShadow = true;
      }
    });
    sharpenTextures(model.root, this.renderer.capabilities.getMaxAnisotropy());
    this.scene.add(model.root);
    this.sceneBox = boundsOf(model.root);
    this.nav.setSceneBox(this.sceneBox);
    if (!keepView) this.nav.frame(this.sceneBox);
    this.lighting.setSceneBox(this.sceneBox);
    this.ground.fit(this.sceneBox);
    this.layers.attach(model.root);
    const initial = this.layers.initialState();
    this.state.layers = keepView
      ? Object.assign(
          initial,
          Object.fromEntries(Object.entries(previousLayers).filter(([name]) => name in initial)),
        )
      : initial;
    // Con terreno propio (TIN), el suelo auxiliar sobra y competiría con él en profundidad
    this.state.ground.enabled = keepView
      ? previousGround
      : ![...this.layers.layers.values()].some((l) => l.kind === 'terrain');
    const located = keepView ? false : this.geolocateSun(model);
    this.info.hide();
    this.applyAll();
    this.emit('model');
    if (located) this.emit('state');
    const misplaced = misplacedLayers(model.root);
    if (misplaced.length) this.notify(misplacedMessage(misplaced), 'warning');
  }

  // --- Aplicar estado -------------------------------------------------------

  applyAll(): void {
    this.applySun();
    this.applyAmbient();
    this.applyShadows();
    this.applyToneMapping();
    this.applyLayers();
    this.applyLights();
    this.applyGround();
    this.applyCamera();
  }

  applyGround(): void {
    this.ground.apply(this.state.ground);
  }

  applySun(): void {
    this.lighting.applySun(this.state.sun);
    this.shadowsDirty = true;
  }

  applyAmbient(): void {
    this.lighting.applyAmbient(this.state.ambient);
  }

  applyShadows(): void {
    this.lighting.applyShadows(this.state.shadows);
    this.shadowsDirty = true;
  }

  applyToneMapping(): void {
    this.lighting.applyToneMapping(this.state.tonemapping);
  }

  applyLayers(): void {
    this.layers.apply(this.state.layers);
  }

  applyLights(): void {
    this.lights.sync(this.state.lights);
  }

  applyCamera(): void {
    this.nav.setType(this.state.camera.type);
    this.nav.setFov(this.state.camera.fovDeg);
  }

  /** Cambia la hora del día conservando fecha y desfase (para el control y la animación). */
  setHours(hours: number): void {
    const { day, utcOffsetH } = splitIso(this.state.sun.isoDate);
    this.state.sun.isoDate = composeIso(day, ((hours % 24) + 24) % 24, utcOffsetH);
    this.applySun();
  }

  // --- Luces adicionales ----------------------------------------------------

  addLight(type: LightType, position: Vec3, target: Vec3): string {
    const light = newLightState(type, position, target);
    this.state.lights.push(light);
    this.applyLights();
    this.lights.select(light.id);
    this.emit('state');
    return light.id;
  }

  removeLight(id: string): void {
    this.state.lights = this.state.lights.filter((l) => l.id !== id);
    this.applyLights();
    this.emit('state');
  }

  private moveLight(id: string, position: Vec3): void {
    const light = this.state.lights.find((l) => l.id === id);
    if (light) light.position = position;
  }

  // --- Interacción ----------------------------------------------------------

  private bindPointer(): void {
    const dom = this.renderer.domElement;
    dom.addEventListener('pointerdown', (e) => this.pointerDown.set(e.clientX, e.clientY));
    dom.addEventListener('pointerup', (e) => {
      if (e.button !== 0 || this.nav.flying || this.lights.dragging) return;
      if (this.pointerDown.distanceTo(new Vector2(e.clientX, e.clientY)) > CLICK_TOLERANCE_PX)
        return;
      this.onClick(e);
    });
    dom.addEventListener('dblclick', (e) => {
      if (this.measure.active) return; // el doble clic serían dos puntos de medida
      const hit = this.hitAt(e);
      if (hit) this.nav.focus(hit.point);
    });
  }

  private hitAt(event: MouseEvent): ReturnType<typeof firstVisibleHit> {
    if (!this.model) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.nav.camera);
    return firstVisibleHit(this.raycaster.intersectObject(this.model.root, true));
  }

  private onClick(event: MouseEvent): void {
    const hit = this.hitAt(event);
    if (this.measure.active) {
      if (hit) this.measure.addPoint(hit.point);
      return;
    }
    if (this.placing) {
      if (!hit) return;
      const normal = hit.face?.normal.clone().transformDirection(hit.object.matrixWorld);
      const position = hit.point.clone();
      if (normal && normal.y < 0.5) position.addScaledVector(normal, LIGHT_PLACEMENT_OFFSET_M);
      else position.y += LIGHT_PLACEMENT_OFFSET_M;
      this.addLight(this.placing, position.toArray(), hit.point.toArray());
      this.placing = null;
      return;
    }
    const info = hit ? describeHit(hit) : null;
    if (info) this.info.show(info);
    else this.info.hide();
    this.selectedObjectId = info?.objectId ?? null;
    this.emit('selection');
  }

  // --- Bucle ----------------------------------------------------------------

  private resize(): void {
    const { clientWidth: w, clientHeight: h } = this.container;
    this.renderer.setSize(w, h);
    this.nav.resize(w, h);
  }

  private frame(now: number): void {
    const dt = Math.min((now - this.lastFrame) / 1000, 0.1);
    this.lastFrame = now;
    if (!this.model) return; // sin modelo no se dibuja nada: la pantalla de inicio lo cubre
    if (this.animation.playing && this.state.sun.mode === 'datetime') {
      this.setHours(splitIso(this.state.sun.isoDate).hours + this.animation.hoursPerSecond * dt);
      this.emit('state');
    }
    this.nav.update(dt);
    const viewFit = this.state.shadows.fit !== 'scene';
    if (this.shadowsDirty || viewFit) {
      this.lighting.fitShadows(this.state.shadows, this.nav.orbit.target, this.nav.distance);
      this.shadowsDirty = false;
    }
    this.lighting.update(now);
    this.lights.update();
    const { clientWidth: w, clientHeight: h } = this.container;
    this.measure.update(this.nav.camera, w, h);
    this.renderer.render(this.scene, this.nav.camera);
    this.stats.update(now);
  }

  get viewportAspect(): number {
    return this.container.clientWidth / Math.max(this.container.clientHeight, 1);
  }

  /** Ajusta sombras y entorno antes de un render fuera del bucle (PNG). */
  prepareFrame(): void {
    this.lighting.fitShadows(this.state.shadows, this.nav.orbit.target, this.nav.distance);
    this.lighting.update(Number.POSITIVE_INFINITY);
  }

  /**
   * Aplica un estado completo (restaurar una vista). Copia los valores DENTRO de los objetos
   * existentes: el panel está enlazado a ellos.
   */
  setState(next: ViewerState, animate: boolean): void {
    const s = this.state;
    Object.assign(s.sun, next.sun);
    Object.assign(s.ambient, next.ambient);
    Object.assign(s.shadows, next.shadows);
    Object.assign(s.ground, next.ground);
    Object.assign(s.tonemapping, next.tonemapping);
    Object.assign(s.camera, next.camera);
    s.lights.splice(0, s.lights.length, ...structuredClone(next.lights));
    for (const [name, layer] of Object.entries(s.layers)) {
      const incoming = next.layers[name];
      layer.visible = incoming?.visible ?? true;
      if (incoming?.colorOverride) layer.colorOverride = incoming.colorOverride;
      else delete layer.colorOverride;
    }
    if (s.ambient.background === 'hdri' && s.ambient.hdri !== this.hdriName) {
      this.notify(
        `La vista usa el HDRI "${s.ambient.hdri ?? '?'}": cárgalo en Entorno para verlo.`,
        'warning',
      );
    }
    this.lights.select(null);
    this.applySun();
    this.applyAmbient();
    this.applyShadows();
    this.applyToneMapping();
    this.applyLayers();
    this.applyLights();
    this.applyGround();
    this.nav.restore(s.camera, animate);
    this.emit('state');
  }

  /** Estado completo serializable (cámara incluida). */
  snapshot(): ViewerState {
    this.state.camera = this.nav.snapshot();
    return structuredClone(this.state);
  }
}
