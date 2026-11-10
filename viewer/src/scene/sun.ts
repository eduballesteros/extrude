import { Vector3 } from 'three';
import SunCalc from 'suncalc';

const DEG = Math.PI / 180;

export interface SunAngles {
  /** Azimut desde el norte, en sentido horario, en [0, 360). */
  azimuthDeg: number;
  elevationDeg: number;
}

/**
 * Posición solar para un instante y lugar.
 * suncalc mide el azimut desde el sur y positivo hacia el oeste; aquí se pasa a la convención
 * geográfica (desde el norte, sentido horario).
 */
export function sunAngles(date: Date, lat: number, lon: number): SunAngles {
  const { azimuth, altitude } = SunCalc.getPosition(date, lat, lon);
  const azimuthDeg = (((azimuth / DEG + 180) % 360) + 360) % 360;
  return { azimuthDeg, elevationDeg: altitude / DEG };
}

/**
 * Vector unitario hacia el sol en coordenadas de la escena (Y-up).
 * El pipeline lleva el +Y del dibujo a −Z, y el +X (este del dibujo) se queda en +X.
 * `northRotationDeg` gira el norte real en sentido horario respecto al +Y del dibujo.
 */
export function sunDirection(angles: SunAngles, northRotationDeg = 0): Vector3 {
  const phi = (angles.azimuthDeg + northRotationDeg) * DEG;
  const el = angles.elevationDeg * DEG;
  return new Vector3(
    Math.cos(el) * Math.sin(phi),
    Math.sin(el),
    -Math.cos(el) * Math.cos(phi),
  ).normalize();
}

/** "2024-06-21T10:30:00+02:00" → hora local decimal (10.5), con el desfase del propio texto. */
export function localHours(isoDate: string): number {
  const match = /T(\d{2}):(\d{2})(?::(\d{2}))?/.exec(isoDate);
  if (!match) throw new Error(`Fecha sin hora: ${isoDate}`);
  return Number(match[1]) + Number(match[2]) / 60 + Number(match[3] ?? 0) / 3600;
}

/** Construye la ISO local "YYYY-MM-DDTHH:MM:SS±HH:MM". */
export function composeIso(day: string, hours: number, utcOffsetH: number): string {
  const clamped = Math.min(Math.max(hours, 0), 24 - 1 / 3600);
  const totalSeconds = Math.round(clamped * 3600);
  const hh = Math.floor(totalSeconds / 3600);
  const mm = Math.floor((totalSeconds % 3600) / 60);
  const ss = totalSeconds % 60;
  const sign = utcOffsetH < 0 ? '-' : '+';
  const abs = Math.abs(utcOffsetH);
  const oh = Math.floor(abs);
  const om = Math.round((abs - oh) * 60);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${day}T${pad(hh)}:${pad(mm)}:${pad(ss)}${sign}${pad(oh)}:${pad(om)}`;
}

/** Partes de una ISO local: día, hora decimal y desfase en horas. */
export function splitIso(isoDate: string): { day: string; hours: number; utcOffsetH: number } {
  const match = /^(\d{4}-\d{2}-\d{2})T[\d:.]+(Z|[+-]\d{2}:\d{2})$/.exec(isoDate);
  if (!match?.[1] || !match[2]) throw new Error(`Fecha ISO no válida: ${isoDate}`);
  const zone = match[2];
  const utcOffsetH =
    zone === 'Z'
      ? 0
      : (zone.startsWith('-') ? -1 : 1) *
        (Number(zone.slice(1, 3)) + Number(zone.slice(4, 6)) / 60);
  return { day: match[1], hours: localHours(isoDate), utcOffsetH };
}

/** Color aproximado de un cuerpo negro (1000–40000 K), algoritmo de Tanner Helland. */
export function kelvinToRgb(kelvin: number): [number, number, number] {
  const t = Math.min(Math.max(kelvin, 1000), 40000) / 100;
  const clamp = (v: number): number => Math.min(Math.max(v, 0), 255) / 255;
  const r = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -0.1332047592);
  const g =
    t <= 66
      ? 99.4708025861 * Math.log(t) - 161.1195681661
      : 288.1221695283 * Math.pow(t - 60, -0.0755148492);
  const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  return [clamp(r), clamp(g), clamp(b)];
}
