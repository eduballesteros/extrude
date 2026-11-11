import type { App } from '../app';
import { composeIso, splitIso, sunTimes } from '../scene/sun';
import type { BackgroundMode, LightState, LightType, ShadowType, ToneMappingType } from '../state';
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

type ShadowMode = 'off' | ShadowType;
const SPEEDS = [0.5, 1, 2, 4];
const MAP_SIZES = [1024, 2048, 4096, 8192];
const LIGHT_NAMES: Record<LightType, string> = { point: 'Puntual', spot: 'Foco', area: 'Área' };
const SVG_NS = 'http://www.w3.org/2000/svg';
const DIAL = 40; // radio del dial solar (px)

export function shadowMode(enabled: boolean, type: ShadowType): ShadowMode {
  return enabled ? type : 'off';
}

/** Exposición lineal ↔ pasos de diafragma (EV). */
export function toEv(exposure: number): number {
  return Math.log2(Math.max(exposure, 1e-6));
}
export function fromEv(ev: number): number {
  return 2 ** ev;
}

export function formatHour(hours: number): string {
  const total = Math.round(hours * 60) % (24 * 60);
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/**
 * Posición del sol en el dial polar (px, y hacia abajo): norte real arriba, azimut horario y
 * elevación hacia el centro (90° = cénit en el centro, 0° = horizonte en el borde).
 */
export function dialPoint(
  azimuthDeg: number,
  elevationDeg: number,
  radius: number,
): [number, number] {
  const r = ((90 - Math.min(Math.max(elevationDeg, -10), 90)) / 90) * radius;
  const a = (azimuthDeg * Math.PI) / 180;
  return [Math.sin(a) * r, -Math.cos(a) * r];
}

/** Inversa de dialPoint (para fijar el sol a mano pulsando en el dial). */
export function dialToSun(
  x: number,
  y: number,
  radius: number,
): { azimuthDeg: number; elevationDeg: number } {
  const r = Math.min(Math.hypot(x, y), radius);
  const az = ((Math.atan2(x, -y) * 180) / Math.PI + 360) % 360;
  return { azimuthDeg: az, elevationDeg: 90 - (r / radius) * 90 };
}

function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function readout(label: string): { element: HTMLElement; value: HTMLElement } {
  const value = h('span', { class: 'readout-value mono' });
  return {
    element: h(
      'div',
      { class: 'readout' },
      h('span', { class: 'readout-label', textContent: label }),
      value,
    ),
    value,
  };
}

/** Panel derecho (inspector técnico): sol, sombras, entorno, postprocesado, cámara y luces. */
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
      this.postSection(),
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

  // --- Luz solar -----------------------------------------------------------------

  private sunSection(): HTMLElement {
    const { app } = this;
    const sun = app.state.sun;
    const clockAside = h('span', { class: 'section-aside mono' });
    const { element, body } = section('Luz solar', true, clockAside);

    // Hora: reloj mono + pista 00:00–24:00 con franja diurna y marcas de orto/ocaso
    const clock = h('span', { class: 'clock mono' });
    const range = h('input', {
      type: 'range',
      min: '0',
      max: '24',
      step: String(1 / 60),
      class: 'timeline-range',
      attrs: { 'aria-label': 'Hora solar' },
    });
    const markers = h('div', { class: 'timeline-markers' });
    const track = h(
      'div',
      { class: 'timeline' },
      h('div', { class: 'timeline-daylight' }),
      markers,
      range,
    );
    const play = h('button', {
      type: 'button',
      class: 'btn btn-ghost btn-sm btn-icon play',
      title: 'Reproducir el día (Espacio)',
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

    // Dial acimut/elevación
    const dialSvg = svgEl('svg', {
      viewBox: `${-DIAL - 8} ${-DIAL - 8} ${2 * DIAL + 16} ${2 * DIAL + 16}`,
      class: 'sun-dial',
      role: 'img',
      'aria-label': 'Posición del sol',
    });
    dialSvg.append(
      svgEl('circle', { r: String(DIAL), class: 'dial-horizon' }),
      svgEl('circle', { r: String(DIAL * (2 / 3)), class: 'dial-ring' }),
      svgEl('circle', { r: String(DIAL / 3), class: 'dial-ring' }),
      svgEl('line', { x1: String(-DIAL), x2: String(DIAL), y1: '0', y2: '0', class: 'dial-ring' }),
      svgEl('line', { y1: String(-DIAL), y2: String(DIAL), x1: '0', x2: '0', class: 'dial-ring' }),
    );
    const north = svgEl('text', { x: '0', y: String(-DIAL - 2), class: 'dial-n' });
    north.textContent = 'N';
    const sunRay = svgEl('line', { x1: '0', y1: '0', class: 'dial-ray' });
    const sunDot = svgEl('circle', { r: '4', class: 'dial-sun' });
    dialSvg.append(north, sunRay, sunDot);
    const setFromDial = (e: PointerEvent): void => {
      const rect = dialSvg.getBoundingClientRect();
      const scale = (2 * DIAL + 16) / rect.width;
      const x = (e.clientX - rect.left) * scale - DIAL - 8;
      const y = (e.clientY - rect.top) * scale - DIAL - 8;
      const pos = dialToSun(x, y, DIAL);
      sun.mode = 'manual';
      sun.azimuthDeg = Math.round(pos.azimuthDeg * 10) / 10;
      sun.elevationDeg = Math.round(pos.elevationDeg * 10) / 10;
      app.applySun();
      this.refresh();
    };
    dialSvg.addEventListener('pointerdown', (e) => {
      if (sun.mode !== 'manual') return;
      dialSvg.setPointerCapture(e.pointerId);
      setFromDial(e);
      const move = (ev: PointerEvent): void => setFromDial(ev);
      const up = (): void => {
        dialSvg.removeEventListener('pointermove', move);
        dialSvg.removeEventListener('pointerup', up);
      };
      dialSvg.addEventListener('pointermove', move);
      dialSvg.addEventListener('pointerup', up);
    });

    const az = readout('Acimut');
    const el = readout('Elevación');
    const rise = readout('Orto');
    const set = readout('Ocaso');

    const date = h('input', { type: 'date', class: 'input mono' });
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
        type: 'text',
        inputMode: 'decimal',
        class: 'input mono',
        spellcheck: false,
      });
      const apply = (): void => {
        const v = Number(input.value.trim().replace(',', '.'));
        const ok = input.value.trim() !== '' && Number.isFinite(v) && v >= min && v <= max;
        input.classList.toggle('is-invalid', !ok);
        if (!ok) return;
        sun[key] = v;
        app.applySun();
        this.refresh();
      };
      input.addEventListener('change', apply);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') input.blur();
      });
      this.refreshers.push(() => {
        if (document.activeElement !== input) {
          input.value = sun[key].toFixed(4);
          input.classList.remove('is-invalid');
        }
      });
      return h(
        'label',
        { class: 'field' },
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
      label: 'Acimut',
      min: 0,
      max: 360,
      step: 0.1,
      value: sun.azimuthDeg,
      unit: '°',
      digits: 1,
      onInput: (v) => ((sun.azimuthDeg = v), app.applySun(), this.refresh()),
    });
    const elevation = slider({
      label: 'Elevación',
      min: -10,
      max: 90,
      step: 0.1,
      value: sun.elevationDeg,
      unit: '°',
      digits: 1,
      onInput: (v) => ((sun.elevationDeg = v), app.applySun(), this.refresh()),
    });
    const manualBox = h('div', { class: 'stack' }, azimuth.element, elevation.element);
    const datetimeBox = h(
      'div',
      { class: 'stack' },
      h(
        'label',
        { class: 'field' },
        h('span', { class: 'field-label', textContent: 'Fecha' }),
        date,
      ),
      utc.element,
      coord('Latitud', 'lat', -90, 90),
      coord('Longitud', 'lon', -180, 180),
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
    const northRot = slider({
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
      clock.textContent = isManual ? '--:--' : formatHour(hours);
      clockAside.textContent = isManual
        ? 'MANUAL'
        : `${formatHour(hours)} UTC${utcOffsetH >= 0 ? '+' : '−'}${Math.abs(utcOffsetH)}`;
      az.value.textContent = `${sun.azimuthDeg.toFixed(1)}°`;
      el.value.textContent = `${sun.elevationDeg.toFixed(1)}°`;
      range.value = String(hours);
      range.disabled = isManual;
      range.style.setProperty('--fill', `${(hours / 24) * 100}%`);
      if (document.activeElement !== date) date.value = day;
      utc.set(String(utcOffsetH));
      play.replaceChildren(icon(app.animation.playing ? 'pause' : 'play', 14));
      play.classList.toggle('is-active', app.animation.playing);
      play.disabled = isManual;
      manual.set(isManual);
      manualBox.hidden = !isManual;
      datetimeBox.hidden = isManual;
      dialSvg.classList.toggle('is-editable', isManual);
      azimuth.set(sun.azimuthDeg);
      elevation.set(sun.elevationDeg);
      intensity.set(sun.intensity);
      temp.set(sun.colorTempK);
      northRot.set(sun.northRotationDeg);
      const [x, y] = dialPoint(sun.azimuthDeg, sun.elevationDeg, DIAL);
      sunDot.setAttribute('cx', x.toFixed(1));
      sunDot.setAttribute('cy', y.toFixed(1));
      sunRay.setAttribute('x2', x.toFixed(1));
      sunRay.setAttribute('y2', y.toFixed(1));
      dialSvg.classList.toggle('is-night', sun.elevationDeg <= 0);
      const times = sunTimes(day, sun.lat, sun.lon, utcOffsetH);
      rise.value.textContent = times.sunrise === null ? '—' : formatHour(times.sunrise);
      set.value.textContent = times.sunset === null ? '—' : formatHour(times.sunset);
      const mark = (hour: number | null): HTMLElement | null =>
        hour === null
          ? null
          : h('span', { class: 'timeline-marker', attrs: { style: `left:${(hour / 24) * 100}%` } });
      markers.replaceChildren(
        ...[mark(times.sunrise), mark(times.sunset)].filter((m): m is HTMLElement => m !== null),
      );
      track.style.setProperty('--day-start', `${((times.sunrise ?? 0) / 24) * 100}%`);
      track.style.setProperty('--day-end', `${((times.sunset ?? 24) / 24) * 100}%`);
    });

    body.append(
      h('div', { class: 'time-row' }, play, clock, speed.element),
      track,
      h(
        'div',
        { class: 'timeline-scale mono' },
        ...['00', '06', '12', '18', '24'].map((t) => h('span', { textContent: t })),
      ),
      h(
        'div',
        { class: 'dial-row' },
        dialSvg,
        h('div', { class: 'readouts' }, az.element, el.element, rise.element, set.element),
      ),
      datetimeBox,
      manual.element,
      manualBox,
      intensity.element,
      temp.element,
      northRot.element,
    );
    return element;
  }

  // --- Sombras -----------------------------------------------------------------

  private shadowSection(): HTMLElement {
    const { app } = this;
    const s = app.state.shadows;
    const { element, body } = section('Sombras');
    const mode = segmented<ShadowMode>(
      [
        { value: 'off', label: 'Apagadas' },
        { value: 'pcf', label: 'PCF', title: 'Percentage-closer filtering' },
        { value: 'vsm', label: 'VSM', title: 'Variance shadow maps (más suaves)' },
      ],
      shadowMode(s.enabled, s.type),
      (m) => {
        s.enabled = m !== 'off';
        if (m !== 'off') s.type = m;
        app.applyShadows();
        this.refresh();
      },
    );
    mode.element.classList.add('segmented-full');
    const radius = slider({
      label: 'Radio',
      min: 0,
      max: 10,
      step: 0.1,
      value: s.radius,
      digits: 1,
      onInput: (v) => ((s.radius = v), app.applyShadows()),
    });
    const bias = slider({
      label: 'Sesgo',
      min: -0.005,
      max: 0.005,
      step: 0.0001,
      value: s.bias,
      digits: 4,
      onInput: (v) => ((s.bias = v), app.applyShadows()),
    });
    const normalBias = slider({
      label: 'Sesgo normal',
      min: 0,
      max: 0.5,
      step: 0.005,
      value: s.normalBias,
      digits: 3,
      onInput: (v) => ((s.normalBias = v), app.applyShadows()),
    });
    const size = select(
      'Mapa',
      MAP_SIZES.map((n) => ({ value: String(n), label: `${n} px` })),
      String(s.mapSize),
      (v) => ((s.mapSize = Number(v)), app.applyShadows()),
    );
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
    const options = h(
      'div',
      { class: 'stack' },
      radius.element,
      bias.element,
      normalBias.element,
      size.element,
      fit.element,
    );
    this.refreshers.push(() => {
      mode.set(shadowMode(s.enabled, s.type));
      radius.set(s.radius);
      bias.set(s.bias);
      normalBias.set(s.normalBias);
      size.set(String(s.mapSize));
      fit.set(s.fit);
      options.hidden = !s.enabled;
    });
    body.append(mode.element, options);
    return element;
  }

  // --- Entorno -----------------------------------------------------------------

  private environmentSection(): HTMLElement {
    const { app } = this;
    const a = app.state.ambient;
    const { element, body } = section('Entorno');
    const background = segmented<BackgroundMode>(
      [
        { value: 'sky', label: 'Cielo procedural' },
        { value: 'color', label: 'Color' },
        { value: 'hdri', label: 'HDRI' },
      ],
      a.background,
      (m) => {
        if (m === 'hdri' && !app.hdriName) {
          this.loadHdri();
          this.refresh();
          return;
        }
        a.background = m;
        app.applyAmbient();
        this.refresh();
      },
    );
    background.element.classList.add('segmented-full');
    const color = colorField(
      'Fondo',
      a.backgroundColor,
      (v) => ((a.backgroundColor = v), app.applyAmbient()),
    );
    const hdriName = h('span', { class: 'mono ellipsis muted' });
    const hdriLoad = button({ label: 'Cargar…', size: 'sm', onClick: () => this.loadHdri() });
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
      label: 'Intensidad',
      min: 0,
      max: 5,
      step: 0.05,
      value: a.hdriIntensity,
      onInput: (v) => ((a.hdriIntensity = v), app.applyAmbient()),
    });
    const hdriBox = h(
      'div',
      { class: 'stack' },
      h(
        'div',
        { class: 'field' },
        h('span', { class: 'field-label', textContent: 'Archivo' }),
        h('div', { class: 'row' }, hdriName, hdriLoad),
      ),
      hdriRotation.element,
      hdriIntensity.element,
    );
    const ambient = slider({
      label: 'Ambiente',
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
      hdriName.textContent = app.hdriName ?? '—';
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

  // --- Postprocesado -----------------------------------------------------------

  private postSection(): HTMLElement {
    const { app } = this;
    const t = app.state.tonemapping;
    const { element, body } = section('Postprocesado');
    const tone = segmented<ToneMappingType>(
      [
        { value: 'agx', label: 'AgX' },
        { value: 'aces', label: 'ACES', title: 'ACES Filmic' },
        { value: 'neutral', label: 'Neutral', title: 'Khronos PBR Neutral' },
        { value: 'none', label: 'Lineal', title: 'Sin tone mapping' },
      ],
      t.type,
      (v) => ((t.type = v), app.applyToneMapping()),
    );
    tone.element.classList.add('segmented-full');
    const exposure = slider({
      label: 'Exposición',
      min: -4,
      max: 4,
      step: 0.1,
      value: toEv(t.exposure),
      unit: 'EV',
      digits: 1,
      onInput: (ev) => ((t.exposure = fromEv(ev)), app.applyToneMapping()),
    });
    this.refreshers.push(() => {
      tone.set(t.type);
      exposure.set(Math.round(toEv(t.exposure) * 10) / 10);
    });
    body.append(
      h(
        'div',
        { class: 'field' },
        h('span', { class: 'field-label', textContent: 'Tone mapping' }),
      ),
      tone.element,
      exposure.element,
    );
    return element;
  }

  // --- Cámara ------------------------------------------------------------------

  private cameraSection(): HTMLElement {
    const { app } = this;
    const { element, body } = section('Cámara', false);
    const fov = slider({
      label: 'FOV vertical',
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
    const fly = button({
      label: 'Modo vuelo',
      size: 'sm',
      title: 'WASD para moverse, Q/E bajar/subir, Mayús rápido, Esc salir',
      onClick: () => app.nav.startFly(),
    });
    this.refreshers.push(() => {
      fov.set(app.state.camera.fovDeg);
      fov.element.hidden = app.state.camera.type !== 'perspective';
    });
    body.append(
      fov.element,
      h(
        'div',
        { class: 'row' },
        fly,
        h('span', { class: 'muted small', textContent: 'WASD · Q/E · Esc' }),
      ),
    );
    return element;
  }

  // --- Luces -------------------------------------------------------------------

  private lightsSection(): HTMLElement {
    const { app } = this;
    const count = h('span', { class: 'section-aside mono' });
    const { element, body } = section('Luces', false, count);
    const add = (type: LightType): HTMLButtonElement =>
      button({
        label: LIGHT_NAMES[type],
        icon: 'plus',
        size: 'sm',
        variant: 'ghost',
        title: `Haz clic en la escena para colocar una luz ${LIGHT_NAMES[type].toLowerCase()}`,
        onClick: () => {
          app.placing = type;
          app.notify(
            `Haz clic sobre la escena para colocar la luz (${LIGHT_NAMES[type].toLowerCase()})`,
          );
        },
      });
    this.refreshers.push(() => {
      count.textContent = String(app.state.lights.length);
      const ids = app.state.lights.map((l) => l.id).join();
      if (ids === this.lightIds) return;
      this.lightIds = ids;
      this.lightsList.replaceChildren(...app.state.lights.map((l, i) => this.lightCard(l, i)));
      if (app.state.lights.length === 0) {
        this.lightsList.append(
          h('p', { class: 'muted small', textContent: 'Sin luces adicionales' }),
        );
      }
    });
    body.append(
      h('div', { class: 'row' }, add('point'), add('spot'), add('area')),
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
        h('span', { class: 'mono muted', textContent: String(index + 1).padStart(2, '0') }),
        h('span', { textContent: LIGHT_NAMES[light.type] }),
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
