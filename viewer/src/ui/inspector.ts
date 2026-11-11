import type { App } from '../app';
import { composeIso, splitIso, sunTimes } from '../scene/sun';
import type { BackgroundMode, LightState, LightType, ToneMappingType } from '../state';
import {
  button,
  colorField,
  h,
  icon,
  pickFile,
  section,
  segmented,
  select,
  slider,
  toggle,
} from './dom';

type ShadowQuality = 'off' | 'medium' | 'high';
const QUALITY_SIZE: Record<Exclude<ShadowQuality, 'off'>, number> = { medium: 2048, high: 4096 };
const SPEEDS = [0.5, 1, 2, 4];
const LIGHT_NAMES: Record<LightType, string> = { point: 'Puntual', spot: 'Foco', area: 'Área' };

export function shadowQuality(enabled: boolean, mapSize: number): ShadowQuality {
  if (!enabled) return 'off';
  return mapSize >= QUALITY_SIZE.high ? 'high' : 'medium';
}

export function formatHour(hours: number): string {
  const total = Math.round(hours * 60) % (24 * 60);
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/** Panel derecho: sol, sombras, entorno, cámara/render y luces. */
export class Inspector {
  readonly element = h('div', { class: 'tab-panel inspector' });
  private readonly refreshers: (() => void)[] = [];
  private readonly lightsList = h('div', { class: 'light-list' });
  private lightIds = '';

  constructor(private readonly app: App) {
    this.element.append(
      this.sunSection(),
      this.shadowSection(),
      this.environmentSection(),
      this.cameraSection(),
      this.lightsSection(),
    );
    app.on('state', () => this.refresh());
    app.on('model', () => this.refresh());
    this.refresh();
  }

  private refresh(): void {
    for (const r of this.refreshers) r();
  }

  // --- Sol ---------------------------------------------------------------------

  private sunSection(): HTMLElement {
    const { app } = this;
    const sun = app.state.sun;
    const { element, body } = section('Sol', 'sun');

    const clock = h('span', { class: 'clock' });
    const readout = h('span', { class: 'readout' });
    const range = h('input', {
      type: 'range',
      min: '0',
      max: '24',
      step: '0.05',
      class: 'timeline-range',
    });
    const markers = h('div', { class: 'timeline-markers' });
    const track = h(
      'div',
      { class: 'timeline' },
      h('div', { class: 'timeline-daylight' }),
      markers,
      range,
    );
    const play = button({
      icon: 'play',
      title: 'Reproducir el día (Espacio)',
      variant: 'primary',
      size: 'sm',
    });
    play.addEventListener('click', () => app.setPlaying(!app.animation.playing));
    const speed = segmented(
      SPEEDS.map((s) => ({ value: String(s), label: `${s}×`, title: `${s} h por segundo` })),
      String(app.animation.hoursPerSecond),
      (v) => (app.animation.hoursPerSecond = Number(v)),
    );

    const setTime = (patch: Partial<{ day: string; hours: number; utcOffsetH: number }>): void => {
      const now = splitIso(sun.isoDate);
      const next = { ...now, ...patch };
      sun.isoDate = composeIso(next.day, next.hours, next.utcOffsetH);
      app.applySun();
      this.refresh();
    };
    range.addEventListener('input', () => setTime({ hours: Number(range.value) }));

    const date = h('input', { type: 'date', class: 'input' });
    date.addEventListener('change', () => {
      if (/^\d{4}-\d{2}-\d{2}$/.test(date.value)) setTime({ day: date.value });
    });
    const offsets = Array.from({ length: 53 }, (_, i) => (i - 24) / 2).filter(
      (v) => v >= -12 && v <= 14,
    );
    const utc = select(
      'Zona',
      offsets.map((v) => ({ value: String(v), label: `UTC${v >= 0 ? '+' : '−'}${Math.abs(v)}` })),
      String(splitIso(sun.isoDate).utcOffsetH),
      (v) => setTime({ utcOffsetH: Number(v) }),
    );
    const coord = (label: string, key: 'lat' | 'lon', min: number, max: number): HTMLElement => {
      const input = h('input', {
        type: 'number',
        class: 'input',
        step: '0.0001',
        min: String(min),
        max: String(max),
      });
      input.addEventListener('change', () => {
        const v = Number(input.value);
        if (Number.isFinite(v) && v >= min && v <= max) {
          sun[key] = v;
          app.applySun();
          this.refresh();
        }
      });
      this.refreshers.push(() => {
        if (document.activeElement !== input) input.value = sun[key].toFixed(4);
      });
      return h(
        'label',
        { class: 'field field-compact' },
        h('span', { class: 'field-label', textContent: label }),
        input,
      );
    };

    const manual = toggle('Posición manual', sun.mode === 'manual', (v) => {
      sun.mode = v ? 'manual' : 'datetime';
      app.applySun();
      this.refresh();
    });
    const azimuth = slider({
      label: 'Azimut',
      min: 0,
      max: 360,
      step: 0.5,
      value: sun.azimuthDeg,
      unit: '°',
      digits: 1,
      onInput: (v) => ((sun.azimuthDeg = v), app.applySun()),
    });
    const elevation = slider({
      label: 'Elevación',
      min: -10,
      max: 90,
      step: 0.5,
      value: sun.elevationDeg,
      unit: '°',
      digits: 1,
      onInput: (v) => ((sun.elevationDeg = v), app.applySun()),
    });
    const manualBox = h('div', { class: 'stack' }, azimuth.element, elevation.element);
    const datetimeBox = h(
      'div',
      { class: 'stack' },
      h(
        'div',
        { class: 'grid-2' },
        h(
          'label',
          { class: 'field field-compact' },
          h('span', { class: 'field-label', textContent: 'Fecha' }),
          date,
        ),
        utc.element,
      ),
      h(
        'div',
        { class: 'grid-2' },
        coord('Latitud', 'lat', -90, 90),
        coord('Longitud', 'lon', -180, 180),
      ),
    );
    const intensity = slider({
      label: 'Intensidad',
      min: 0,
      max: 10,
      step: 0.05,
      value: sun.intensity,
      onInput: (v) => ((sun.intensity = v), app.applySun()),
    });
    const temp = slider({
      label: 'Temperatura',
      min: 2000,
      max: 12000,
      step: 50,
      value: sun.colorTempK,
      unit: 'K',
      digits: 0,
      onInput: (v) => ((sun.colorTempK = v), app.applySun()),
    });
    const north = slider({
      label: 'Norte real',
      min: -180,
      max: 180,
      step: 0.5,
      value: sun.northRotationDeg,
      unit: '°',
      digits: 1,
      onInput: (v) => ((sun.northRotationDeg = v), app.applySun()),
    });

    this.refreshers.push(() => {
      const { day, hours, utcOffsetH } = splitIso(sun.isoDate);
      const isManual = sun.mode === 'manual';
      clock.textContent = isManual ? 'Manual' : formatHour(hours);
      readout.textContent = `Az ${sun.azimuthDeg.toFixed(1)}° · El ${sun.elevationDeg.toFixed(1)}°`;
      range.value = String(hours);
      range.disabled = isManual;
      range.style.setProperty('--fill', `${(hours / 24) * 100}%`);
      if (document.activeElement !== date) date.value = day;
      utc.set(String(utcOffsetH));
      play.replaceChildren(icon(app.animation.playing ? 'pause' : 'play'));
      play.disabled = isManual;
      manual.set(isManual);
      manualBox.hidden = !isManual;
      datetimeBox.hidden = isManual;
      azimuth.set(sun.azimuthDeg);
      elevation.set(sun.elevationDeg);
      intensity.set(sun.intensity);
      temp.set(sun.colorTempK);
      north.set(sun.northRotationDeg);
      const times = sunTimes(day, sun.lat, sun.lon, utcOffsetH);
      const mark = (
        hour: number | null,
        iconName: 'sunrise' | 'sunset',
        label: string,
      ): HTMLElement | null =>
        hour === null
          ? null
          : h(
              'span',
              {
                class: 'timeline-marker',
                title: `${label} ${formatHour(hour)}`,
                attrs: { style: `left:${(hour / 24) * 100}%` },
              },
              icon(iconName, 12),
            );
      markers.replaceChildren(
        ...[
          mark(times.sunrise, 'sunrise', 'Amanecer'),
          mark(times.sunset, 'sunset', 'Ocaso'),
        ].filter((m): m is HTMLElement => m !== null),
      );
      const day0 = times.sunrise ?? 0;
      const day1 = times.sunset ?? 24;
      track.style.setProperty('--day-start', `${(day0 / 24) * 100}%`);
      track.style.setProperty('--day-end', `${(day1 / 24) * 100}%`);
    });

    body.append(
      h('div', { class: 'sun-header' }, clock, readout),
      track,
      h(
        'div',
        { class: 'timeline-scale' },
        ...['0', '6', '12', '18', '24'].map((t) => h('span', { textContent: t })),
      ),
      h('div', { class: 'row' }, play, speed.element),
      datetimeBox,
      manual.element,
      manualBox,
      intensity.element,
      temp.element,
      north.element,
    );
    return element;
  }

  // --- Sombras -----------------------------------------------------------------

  private shadowSection(): HTMLElement {
    const { app } = this;
    const s = app.state.shadows;
    const { element, body } = section('Sombras', 'shadow');
    const quality = segmented<ShadowQuality>(
      [
        { value: 'off', label: 'No' },
        { value: 'medium', label: 'Media 2K' },
        { value: 'high', label: 'Alta 4K' },
      ],
      shadowQuality(s.enabled, s.mapSize),
      (q) => {
        s.enabled = q !== 'off';
        if (q !== 'off') s.mapSize = QUALITY_SIZE[q];
        app.applyShadows();
        this.refresh();
      },
    );
    const softness = slider({
      label: 'Suavizado',
      min: 0,
      max: 10,
      step: 0.5,
      value: s.radius,
      digits: 1,
      onInput: (v) => ((s.radius = v), app.applyShadows()),
    });
    const fit = select(
      'Ajuste',
      [
        { value: 'auto', label: 'Automático' },
        { value: 'scene', label: 'Toda la escena' },
        { value: 'view', label: 'Zona visible' },
      ],
      s.fit,
      (v) => ((s.fit = v), app.applyShadows()),
    );
    const bias = slider({
      label: 'Bias',
      min: -0.005,
      max: 0.005,
      step: 0.0001,
      value: s.bias,
      digits: 4,
      onInput: (v) => ((s.bias = v), app.applyShadows()),
    });
    const normalBias = slider({
      label: 'Normal bias',
      min: 0,
      max: 0.5,
      step: 0.005,
      value: s.normalBias,
      digits: 3,
      onInput: (v) => ((s.normalBias = v), app.applyShadows()),
    });
    const filter = segmented(
      [
        { value: 'pcf', label: 'PCF' },
        { value: 'vsm', label: 'VSM' },
      ],
      s.type,
      (v) => ((s.type = v), app.applyShadows()),
    );
    const advanced = h(
      'details',
      { class: 'advanced' },
      h('summary', { textContent: 'Avanzado' }),
      h(
        'div',
        { class: 'stack' },
        fit.element,
        h(
          'div',
          { class: 'field' },
          h('span', { class: 'field-label', textContent: 'Filtro' }),
          filter.element,
        ),
        bias.element,
        normalBias.element,
      ),
    );
    this.refreshers.push(() => {
      quality.set(shadowQuality(s.enabled, s.mapSize));
      softness.set(s.radius);
      fit.set(s.fit);
      filter.set(s.type);
      bias.set(s.bias);
      normalBias.set(s.normalBias);
      softness.element.hidden = !s.enabled;
      advanced.hidden = !s.enabled;
    });
    body.append(quality.element, softness.element, advanced);
    return element;
  }

  // --- Entorno -----------------------------------------------------------------

  private environmentSection(): HTMLElement {
    const { app } = this;
    const a = app.state.ambient;
    const { element, body } = section('Entorno', 'globe');
    const apply = (): void => {
      app.applyAmbient();
      this.refresh();
    };
    const background = segmented<BackgroundMode>(
      [
        { value: 'sky', label: 'Cielo', icon: 'sky' },
        { value: 'color', label: 'Color', icon: 'palette' },
        { value: 'hdri', label: 'HDRI', icon: 'globe' },
      ],
      a.background,
      (mode) => {
        if (mode === 'hdri' && !app.hdriName) {
          this.loadHdri();
          return;
        }
        a.background = mode;
        apply();
      },
    );
    const color = colorField(
      'Color de fondo',
      a.backgroundColor,
      (v) => ((a.backgroundColor = v), app.applyAmbient()),
    );
    const hdriName = h('span', { class: 'muted ellipsis' });
    const hdriLoad = button({
      label: 'Cargar HDRI…',
      icon: 'upload',
      size: 'sm',
      onClick: () => this.loadHdri(),
    });
    const hdriRotation = slider({
      label: 'Rotación',
      min: -180,
      max: 180,
      step: 1,
      value: a.hdriRotationDeg,
      unit: '°',
      digits: 0,
      onInput: (v) => ((a.hdriRotationDeg = v), app.applyAmbient()),
    });
    const hdriIntensity = slider({
      label: 'Intensidad HDRI',
      min: 0,
      max: 5,
      step: 0.05,
      value: a.hdriIntensity,
      onInput: (v) => ((a.hdriIntensity = v), app.applyAmbient()),
    });
    const hdriBox = h(
      'div',
      { class: 'stack' },
      h('div', { class: 'row' }, hdriLoad, hdriName),
      hdriRotation.element,
      hdriIntensity.element,
    );
    const ambient = slider({
      label: 'Luz ambiente',
      min: 0,
      max: 3,
      step: 0.05,
      value: a.intensity,
      onInput: (v) => ((a.intensity = v), app.applyAmbient()),
    });
    const ground = toggle('Suelo con sombras', app.state.ground.enabled, (v) => {
      app.state.ground.enabled = v;
      app.applyGround();
    });
    this.refreshers.push(() => {
      background.set(a.background);
      color.set(a.backgroundColor);
      color.element.hidden = a.background !== 'color';
      hdriBox.hidden = a.background !== 'hdri';
      hdriName.textContent = app.hdriName ?? 'Ninguno';
      hdriRotation.set(a.hdriRotationDeg);
      hdriIntensity.set(a.hdriIntensity);
      ambient.set(a.intensity);
      ground.set(app.state.ground.enabled);
    });
    body.append(background.element, color.element, hdriBox, ambient.element, ground.element);
    return element;
  }

  private loadHdri(): void {
    pickFile('.hdr,.exr', (file) => {
      this.app
        .loadHdri(file)
        .then(() => this.app.notify(`HDRI ${file.name} cargado`, 'success'))
        .catch((e: unknown) => this.app.notify(`No se pudo cargar el HDRI: ${String(e)}`, 'error'));
    });
  }

  // --- Cámara y render ---------------------------------------------------------

  private cameraSection(): HTMLElement {
    const { app } = this;
    const { element, body } = section('Cámara y render', 'camera');
    const fov = slider({
      label: 'Campo de visión',
      min: 10,
      max: 100,
      step: 1,
      value: app.state.camera.fovDeg,
      unit: '°',
      digits: 0,
      onInput: (v) => {
        app.state.camera.fovDeg = v;
        app.applyCamera();
      },
    });
    const tone = select<ToneMappingType>(
      'Tone mapping',
      [
        { value: 'agx', label: 'AgX' },
        { value: 'aces', label: 'ACES Filmic' },
        { value: 'neutral', label: 'Khronos Neutral' },
        { value: 'none', label: 'Ninguno' },
      ],
      app.state.tonemapping.type,
      (v) => ((app.state.tonemapping.type = v), app.applyToneMapping()),
    );
    const exposure = slider({
      label: 'Exposición',
      min: 0.1,
      max: 4,
      step: 0.05,
      value: app.state.tonemapping.exposure,
      onInput: (v) => ((app.state.tonemapping.exposure = v), app.applyToneMapping()),
    });
    const fly = button({
      label: 'Modo vuelo',
      icon: 'camera',
      size: 'sm',
      title: 'WASD para moverse, Q/E bajar/subir, Shift rápido, Esc salir',
      onClick: () => app.nav.startFly(),
    });
    this.refreshers.push(() => {
      fov.set(app.state.camera.fovDeg);
      fov.element.hidden = app.state.camera.type !== 'perspective';
      tone.set(app.state.tonemapping.type);
      exposure.set(app.state.tonemapping.exposure);
    });
    body.append(fov.element, tone.element, exposure.element, fly);
    return element;
  }

  // --- Luces -------------------------------------------------------------------

  private lightsSection(): HTMLElement {
    const { app } = this;
    const { element, body } = section('Luces', 'bulb', false);
    const add = (type: LightType): HTMLButtonElement =>
      button({
        label: LIGHT_NAMES[type],
        icon: 'plus',
        size: 'sm',
        title: `Haz clic en la escena para colocar una luz ${LIGHT_NAMES[type].toLowerCase()}`,
        onClick: () => {
          app.placing = type;
          app.notify(
            `Haz clic sobre la escena para colocar la luz (${LIGHT_NAMES[type].toLowerCase()})`,
          );
        },
      });
    this.refreshers.push(() => {
      const ids = app.state.lights.map((l) => l.id).join();
      if (ids === this.lightIds) return;
      this.lightIds = ids;
      this.lightsList.replaceChildren(...app.state.lights.map((l, i) => this.lightCard(l, i)));
      if (app.state.lights.length === 0) {
        this.lightsList.append(
          h('p', { class: 'muted small', textContent: 'Sin luces adicionales.' }),
        );
      }
    });
    body.append(
      h('div', { class: 'row wrap' }, add('point'), add('spot'), add('area')),
      this.lightsList,
    );
    return element;
  }

  private lightCard(light: LightState, index: number): HTMLElement {
    const { app } = this;
    const apply = (): void => app.applyLights();
    const fields: HTMLElement[] = [
      colorField('Color', light.color, (v) => ((light.color = v), apply())).element,
      slider({
        label: 'Intensidad',
        min: 0,
        max: light.type === 'area' ? 100 : 20000,
        step: 1,
        value: light.intensity,
        digits: 0,
        onInput: (v) => ((light.intensity = v), apply()),
      }).element,
    ];
    if (light.type !== 'area') {
      fields.push(
        slider({
          label: 'Distancia',
          min: 0,
          max: 500,
          step: 1,
          value: light.distance ?? 0,
          unit: 'm',
          digits: 0,
          onInput: (v) => ((light.distance = v), apply()),
        }).element,
      );
    }
    if (light.type === 'spot') {
      fields.push(
        slider({
          label: 'Ángulo',
          min: 1,
          max: 89,
          step: 1,
          value: light.angleDeg ?? 30,
          unit: '°',
          digits: 0,
          onInput: (v) => ((light.angleDeg = v), apply()),
        }).element,
      );
    }
    if (light.type === 'area') {
      fields.push(
        slider({
          label: 'Ancho',
          min: 0.1,
          max: 50,
          step: 0.1,
          value: light.width ?? 4,
          unit: 'm',
          digits: 1,
          onInput: (v) => ((light.width = v), apply()),
        }).element,
        slider({
          label: 'Alto',
          min: 0.1,
          max: 50,
          step: 0.1,
          value: light.height ?? 2,
          unit: 'm',
          digits: 1,
          onInput: (v) => ((light.height = v), apply()),
        }).element,
      );
    }
    return h(
      'details',
      { class: 'light-card' },
      h(
        'summary',
        {},
        icon('bulb', 14),
        h('span', { textContent: `${index + 1}. ${LIGHT_NAMES[light.type]}` }),
        h(
          'span',
          { class: 'row-actions' },
          button({
            icon: 'focus',
            title: 'Mover con el gizmo',
            variant: 'ghost',
            size: 'sm',
            onClick: () => app.lights.select(light.id),
          }),
          button({
            icon: 'trash',
            title: 'Eliminar',
            variant: 'ghost',
            size: 'sm',
            onClick: () => app.removeLight(light.id),
          }),
        ),
      ),
      h('div', { class: 'stack' }, ...fields),
    );
  }
}
