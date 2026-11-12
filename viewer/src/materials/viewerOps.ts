/**
 * Herramientas que ejecuta el visor (no el servidor): `view.create` y `scene.set_sun`.
 *
 * Protocolo de dos fases, para que una confirmación mixta sea todo o nada:
 * 1. `prepareViewerOps` calcula, SIN tocar nada, el estado final de cada vista (cámara exacta del
 *    preset y del encuadre, sin depender de animaciones) y del sol. Si algo no es posible (p. ej.
 *    «atardecer» sin puesta de sol en esa latitud y fecha) falla aquí, antes de llamar al servidor.
 * 2. El servidor confirma la revisión guardando ese estado preparado (`viewer`); después
 *    `commitPrepared` lo aplica. Deshacer, rehacer y reabrir usan el estado guardado en la
 *    revisión, no memoria del navegador.
 *
 * Horas con nombre, reglas explícitas sobre las horas solares de la ubicación del proyecto:
 * `sunrise` = salida + 20 min, `sunset` = puesta − 20 min (aún con luz), `noon` = mediodía solar.
 */
import { Box3 } from 'three';
import type { App } from '../app';
import { objectBoxes } from '../scene/highlight';
import type { ViewPreset } from '../scene/navigation';
import { composeIso, splitIso, sunTimes } from '../scene/sun';
import type { SunState, ViewerState } from '../state';
import type { ViewerOp } from './ai';

export const NAMED_OFFSET_H = 20 / 60;

/** Lo que el visor necesita de la lista de vistas. */
export interface ViewsTarget {
  remove(id: string): void;
  /** Añade una vista con id, nombre y estado dados (no hace nada si ya existe). */
  insertPrepared(id: string, name: string, state: ViewerState): void;
  has(id: string): boolean;
}

/** Estado del visor de una revisión: lo que se guarda en el servidor con ella. */
export interface ViewerEffects {
  views: { id: string; name: string; state: ViewerState }[];
  sunBefore: SunState | null;
  sunAfter: SunState | null;
}

/** Fecha ISO del sol para una hora «HH:MM» o con nombre, en la zona horaria del proyecto. */
export function resolveSunIso(sun: SunState, time: string, date: string | null): string {
  const current = splitIso(sun.isoDate);
  const day = date ?? current.day;
  let hours: number;
  const match = /^(\d{2}):(\d{2})$/.exec(time);
  if (match) {
    hours = Number(match[1]) + Number(match[2]) / 60;
  } else {
    const times = sunTimes(day, sun.lat, sun.lon, current.utcOffsetH);
    if (time === 'sunrise' && times.sunrise !== null) hours = times.sunrise + NAMED_OFFSET_H;
    else if (time === 'sunset' && times.sunset !== null) hours = times.sunset - NAMED_OFFSET_H;
    else if (time === 'noon') hours = times.noon;
    else {
      throw new Error(
        `No hay ${time === 'sunrise' ? 'salida' : 'puesta'} de sol el ${day} en latitud ${sun.lat.toFixed(2)}: indica una hora concreta.`,
      );
    }
  }
  return composeIso(day, hours, current.utcOffsetH);
}

/**
 * Fase 1: estado final de cada operación, sin modificar el visor. Las operaciones se encadenan
 * (el sol de una vista posterior parte del de la anterior). Lanza si alguna no es posible.
 */
export function prepareViewerOps(app: App, ops: ViewerOp[]): ViewerEffects {
  const start = structuredClone(app.snapshot());
  let sun: SunState = start.sun;
  let changedSun = false;
  const views: ViewerEffects['views'] = [];
  for (const op of ops) {
    const args = op.args as { time?: string | null; date?: string | null; preset?: ViewPreset };
    if (typeof args.time === 'string') {
      sun = { ...sun, mode: 'datetime', isoDate: resolveSunIso(sun, args.time, args.date ?? null) };
      changedSun = true;
    }
    if (op.tool === 'view.create') {
      let box: Box3 | undefined;
      if (op.objectIds.length && app.model) {
        const boxes = [...objectBoxes(app.model.root, new Set(op.objectIds)).values()];
        if (boxes.length) box = boxes.reduce((all, b) => all.union(b), new Box3());
      }
      const camera = app.nav.presetCamera(args.preset ?? 'iso', box);
      const name = typeof op.args['name'] === 'string' ? op.args['name'] : 'Vista';
      views.push({ id: crypto.randomUUID(), name, state: { ...start, camera, sun } });
    }
  }
  return { views, sunBefore: changedSun ? start.sun : null, sunAfter: changedSun ? sun : null };
}

/** Previsualizar: muestra la última vista preparada (o solo el sol) sin guardar nada. */
export function previewPrepared(app: App, effects: ViewerEffects): ViewerState {
  const before = app.snapshot();
  const last = effects.views.at(-1);
  if (last) app.setState(last.state, false);
  else if (effects.sunAfter) app.setState({ ...app.snapshot(), sun: effects.sunAfter }, false);
  return before;
}

export function restore(app: App, state: ViewerState): void {
  app.setState(state, false);
}

/** Fase 2 (tras confirmar en el servidor): crea las vistas preparadas y aplica el sol final. */
export function commitPrepared(app: App, views: ViewsTarget, effects: ViewerEffects): void {
  for (const view of effects.views) {
    app.setState(view.state, false); // la miniatura sale de esta cámara
    views.insertPrepared(view.id, view.name, view.state);
  }
  if (effects.sunAfter) app.setState({ ...app.snapshot(), sun: effects.sunAfter }, false);
}

/** Deshacer una revisión: quita sus vistas y devuelve el sol que había antes. */
export function undoEffects(app: App, views: ViewsTarget, effects: ViewerEffects): void {
  for (const view of effects.views) views.remove(view.id);
  if (effects.sunBefore) app.setState({ ...app.snapshot(), sun: effects.sunBefore }, false);
}

/** Rehacer (o reabrir): repone sus vistas y el sol que dejó, aunque no creara vistas. */
export function redoEffects(app: App, views: ViewsTarget, effects: ViewerEffects): void {
  const camera = app.snapshot();
  for (const view of effects.views) {
    if (views.has(view.id)) continue;
    app.setState(view.state, false);
    views.insertPrepared(view.id, view.name, view.state);
  }
  app.setState({ ...camera, ...(effects.sunAfter ? { sun: effects.sunAfter } : {}) }, false);
}
