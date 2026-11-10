import GUI, { type Controller } from 'lil-gui';
import type { App } from '../app';
import { LayerManager } from '../scene/layers';
import { composeIso, splitIso } from '../scene/sun';
import type { LightState, LightType } from '../state';

const SHADOW_SIZES = [1024, 2048, 4096, 8192];
const TONE_MAPPINGS = { AgX: 'agx', ACES: 'aces', Neutral: 'neutral', Ninguno: 'none' };
const LIGHT_TYPES: Record<string, LightType> = { Puntual: 'point', Foco: 'spot', Área: 'area' };
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function pickFile(accept: string, onFile: (file: File) => void): void {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = accept;
  input.addEventListener('change', () => {
    const file = input.files?.[0];
    if (file) onFile(file);
  });
  input.click();
}

/** Panel de control (lil-gui) enlazado al estado de la App. */
export class ControlPanel {
  private readonly gui = new GUI({ title: 'Extrude', width: 320 });
  private readonly time: { day: string; hours: number; utcOffsetH: number };
  private readonly manualControllers: Controller[] = [];
  private readonly datetimeControllers: Controller[] = [];
  private layersFolder: GUI;
  private lightsFolder: GUI;
  private readonly newLight = { type: 'point' as LightType };

  constructor(private readonly app: App) {
    this.time = splitIso(app.state.sun.isoDate);
    this.buildCamera();
    this.buildSun();
    this.buildShadows();
    this.buildEnvironment();
    this.lightsFolder = this.buildLights();
    this.buildRender();
    this.layersFolder = this.gui.addFolder('Capas');
    app.on('model', () => this.rebuildLayers());
    app.on('state', () => this.refresh());
  }

  private buildCamera(): void {
    const { app } = this;
    const folder = this.gui.addFolder('Cámara');
    const camera = app.state.camera;
    folder
      .add(camera, 'type', { Perspectiva: 'perspective', Ortográfica: 'orthographic' })
      .name('Proyección')
      .onChange(() => app.applyCamera());
    folder
      .add(camera, 'fovDeg', 10, 100, 1)
      .name('FOV (°)')
      .onChange(() => app.applyCamera());
    folder.add({ frame: () => app.nav.frame() }, 'frame').name('Encuadrar todo');
    folder.add({ fly: () => app.nav.startFly() }, 'fly').name('Vuelo (WASD, Q/E, Shift; Esc sale)');
  }

  private buildSun(): void {
    const { app } = this;
    const sun = app.state.sun;
    const folder = this.gui.addFolder('Sol');
    const applyTime = (): void => {
      if (!DAY_PATTERN.test(this.time.day)) return;
      sun.isoDate = composeIso(this.time.day, this.time.hours, this.time.utcOffsetH);
      app.applySun();
    };
    folder
      .add(sun, 'mode', { 'Fecha y lugar': 'datetime', Manual: 'manual' })
      .name('Modo')
      .onChange(() => {
        this.updateSunMode();
        app.applySun();
      });
    this.datetimeControllers.push(
      folder.add(this.time, 'day').name('Fecha (AAAA-MM-DD)').onFinishChange(applyTime),
      folder.add(this.time, 'hours', 0, 23.99, 0.05).name('Hora local').onChange(applyTime),
      folder.add(this.time, 'utcOffsetH', -12, 14, 0.5).name('UTC ±h').onChange(applyTime),
      folder
        .add(sun, 'lat', -90, 90, 0.0001)
        .name('Latitud')
        .onChange(() => app.applySun()),
      folder
        .add(sun, 'lon', -180, 180, 0.0001)
        .name('Longitud')
        .onChange(() => app.applySun()),
      folder.add(app.animation, 'playing').name('▶ Animar el día'),
      folder.add(app.animation, 'hoursPerSecond', 0.1, 6, 0.1).name('Horas por segundo'),
    );
    this.manualControllers.push(
      folder
        .add(sun, 'azimuthDeg', 0, 360, 0.1)
        .name('Azimut (°)')
        .onChange(() => app.applySun()),
      folder
        .add(sun, 'elevationDeg', -10, 90, 0.1)
        .name('Elevación (°)')
        .onChange(() => app.applySun()),
    );
    folder
      .add(sun, 'northRotationDeg', -180, 180, 0.5)
      .name('Norte (° horario)')
      .onChange(() => app.applySun());
    folder
      .add(sun, 'intensity', 0, 10, 0.05)
      .name('Intensidad')
      .onChange(() => app.applySun());
    folder
      .add(sun, 'colorTempK', 2000, 12000, 50)
      .name('Temperatura (K)')
      .onChange(() => app.applySun());
    this.updateSunMode();
  }

  private updateSunMode(): void {
    const manual = this.app.state.sun.mode === 'manual';
    for (const c of this.manualControllers) c.enable(manual).listen(!manual);
    for (const c of this.datetimeControllers) c.enable(!manual);
  }

  private buildShadows(): void {
    const { app } = this;
    const shadows = app.state.shadows;
    const folder = this.gui.addFolder('Sombras');
    const apply = (): void => app.applyShadows();
    folder.add(shadows, 'enabled').name('Activas').onChange(apply);
    folder.add(shadows, 'mapSize', SHADOW_SIZES).name('Resolución').onChange(apply);
    folder.add(shadows, 'type', { PCF: 'pcf', VSM: 'vsm' }).name('Filtro').onChange(apply);
    folder.add(shadows, 'radius', 0, 10, 0.5).name('Suavizado').onChange(apply);
    folder.add(shadows, 'bias', -0.005, 0.005, 0.0001).name('Bias').onChange(apply);
    folder.add(shadows, 'normalBias', 0, 0.5, 0.005).name('Normal bias').onChange(apply);
    folder
      .add(shadows, 'fit', {
        Automático: 'auto',
        'Toda la escena': 'scene',
        'Zona visible': 'view',
      })
      .name('Ajuste')
      .onChange(apply);
    folder.close();
  }

  private buildEnvironment(): void {
    const { app } = this;
    const ambient = app.state.ambient;
    const folder = this.gui.addFolder('Entorno');
    const apply = (): void => app.applyAmbient();
    folder
      .add(ambient, 'background', { 'Cielo procedural': 'sky', HDRI: 'hdri', Color: 'color' })
      .name('Fondo')
      .onChange(apply);
    folder.addColor(ambient, 'backgroundColor').name('Color de fondo').onChange(apply);
    folder.add(ambient, 'intensity', 0, 3, 0.05).name('Luz ambiente').onChange(apply);
    folder.addColor(ambient, 'skyColor').name('Color cielo').onChange(apply);
    folder.addColor(ambient, 'groundColor').name('Color suelo').onChange(apply);
    folder
      .add(
        {
          load: () =>
            pickFile('.hdr,.exr', (file) => {
              app.loadHdri(file).catch((error: unknown) => {
                console.error(error);
                app.notify(`Error al cargar el HDRI: ${String(error)}`);
              });
            }),
        },
        'load',
      )
      .name('Cargar HDRI (.hdr/.exr)…');
    folder.add(ambient, 'hdriRotationDeg', -180, 180, 1).name('Rotación HDRI (°)').onChange(apply);
    folder.add(ambient, 'hdriIntensity', 0, 5, 0.05).name('Intensidad HDRI').onChange(apply);
    folder
      .add(app.state.ground, 'enabled')
      .name('Suelo (recibe sombras)')
      .onChange(() => app.applyGround());
    folder
      .addColor(app.state.ground, 'color')
      .name('Color del suelo')
      .onChange(() => app.applyGround());
    folder.close();
  }

  private buildLights(): GUI {
    const { app } = this;
    const folder = this.gui.addFolder('Luces');
    folder.add(this.newLight, 'type', LIGHT_TYPES).name('Tipo nuevo');
    folder
      .add({ place: () => (app.placing = this.newLight.type) }, 'place')
      .name('Colocar con clic en la escena');
    folder.close();
    return folder;
  }

  private rebuildLights(): void {
    for (const child of [...this.lightsFolder.folders]) child.destroy();
    this.app.state.lights.forEach((light, index) => this.addLightFolder(light, index));
  }

  private addLightFolder(light: LightState, index: number): void {
    const { app } = this;
    const apply = (): void => app.applyLights();
    const names: Record<LightType, string> = { point: 'Puntual', spot: 'Foco', area: 'Área' };
    const folder = this.lightsFolder.addFolder(`${index + 1}. ${names[light.type]}`);
    folder.addColor(light, 'color').name('Color').onChange(apply);
    folder
      .add(light, 'intensity', 0, light.type === 'area' ? 100 : 20000, 1)
      .name('Intensidad')
      .onChange(apply);
    if (light.type !== 'area')
      folder.add(light, 'distance', 0, 500, 1).name('Distancia (0 = ∞)').onChange(apply);
    if (light.type === 'spot')
      folder.add(light, 'angleDeg', 1, 89, 1).name('Ángulo (°)').onChange(apply);
    if (light.type === 'area') {
      folder.add(light, 'width', 0.1, 50, 0.1).name('Ancho (m)').onChange(apply);
      folder.add(light, 'height', 0.1, 50, 0.1).name('Alto (m)').onChange(apply);
    }
    folder.add({ select: () => app.lights.select(light.id) }, 'select').name('Mover (gizmo)');
    folder.add({ remove: () => app.removeLight(light.id) }, 'remove').name('Eliminar');
  }

  private buildRender(): void {
    const { app } = this;
    const folder = this.gui.addFolder('Render');
    const tone = app.state.tonemapping;
    folder
      .add(tone, 'type', TONE_MAPPINGS)
      .name('Tone mapping')
      .onChange(() => app.applyToneMapping());
    folder
      .add(tone, 'exposure', 0.1, 4, 0.05)
      .name('Exposición')
      .onChange(() => app.applyToneMapping());
  }

  private rebuildLayers(): void {
    const { app } = this;
    this.layersFolder.destroy();
    this.layersFolder = this.gui.addFolder('Capas');
    const states = app.state.layers;
    const apply = (): void => {
      app.applyLayers();
      this.layersFolder.controllersRecursive().forEach((c) => c.updateDisplay());
    };
    this.layersFolder
      .add({ all: () => (LayerManager.showAll(states), apply()) }, 'all')
      .name('Mostrar todas');
    for (const info of app.layers.layers.values()) {
      const state = states[info.name];
      if (!state) continue;
      const folder = this.layersFolder.addFolder(info.name);
      folder
        .add(state, 'visible')
        .name(`Visible (${info.kind}, ${info.triangles.toLocaleString('es-ES')} tri)`)
        .onChange(apply);
      const color = {
        value: state.colorOverride ?? `#${info.originalColors[0]?.getHexString() ?? 'cccccc'}`,
      };
      folder
        .addColor(color, 'value')
        .name('Color')
        .onChange((value: string) => {
          state.colorOverride = value;
          apply();
        });
      folder
        .add({ reset: () => (delete state.colorOverride, apply()) }, 'reset')
        .name('Color original');
      folder
        .add({ isolate: () => (LayerManager.isolate(states, info.name), apply()) }, 'isolate')
        .name('Aislar');
      folder.close();
    }
  }

  private refresh(): void {
    Object.assign(this.time, splitIso(this.app.state.sun.isoDate));
    if (this.lightsFolder.folders.length !== this.app.state.lights.length) this.rebuildLights();
    this.gui.controllersRecursive().forEach((c) => c.updateDisplay());
  }
}
