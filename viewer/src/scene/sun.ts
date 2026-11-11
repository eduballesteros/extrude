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

export interface SunTimes {
  /** Horas locales decimales; null en día o noche polar. */
  sunrise: number | null;
  sunset: number | null;
  noon: number;
}

/** Amanecer, mediodía solar y ocaso en hora local (con el desfase indicado). */
export function sunTimes(day: string, lat: number, lon: number, utcOffsetH: number): SunTimes {
  const noonLocal = new Date(composeIso(day, 12, utcOffsetH));
  const times = SunCalc.getTimes(noonLocal, lat, lon);
  const local = (date: Date): number | null => {
    const t = date.getTime();
    if (Number.isNaN(t)) return null;
    const hours = (((t / 3_600_000 + utcOffsetH) % 24) + 24) % 24;
    return hours;
  };
  return {
    sunrise: local(times.sunrise),
    sunset: local(times.sunset),
    noon: local(times.solarNoon) ?? 12,
  };
}

/** El sol se apaga gradualmente entre estas elevaciones (crepúsculo). */
export const TWILIGHT_DEG: [number, number] = [-2, 4];

/** Factor 0..1 de luz solar según la elevación (suave en el crepúsculo). */
export function daylightFactor(elevationDeg: number): number {
  const [a, b] = TWILIGHT_DEG;
  const t = Math.min(Math.max((elevationDeg - a) / (b - a), 0), 1);
  return t * t * (3 - 2 * t);
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

/** Diferencia máxima (h) entre la zona horaria y la hora solar media antes de corregirla. */
export const MAX_ZONE_DRIFT_H = 3.5;

export interface Geolocation {
  lat: number;
  lon: number;
  /** Desfase UTC (h) para la fecha; distinto del anterior solo si no encajaba con la longitud. */
  utcOffsetH: number;
  /** El desfase se ha cambiado por uno aproximado (round(lon / 15)). */
  zoneChanged: boolean;
  isoDate: string;
}

/**
 * Sitúa el sol en el centro geográfico del modelo (`center_lat` / `center_lon` del pipeline).
 * Conserva la fecha y la hora local; la zona horaria solo se cambia si la actual no puede ser de
 * ese lugar (más de MAX_ZONE_DRIFT_H horas de la hora solar media), p. ej. UTC+2 en Tokio.
 */
export function geolocate(
  isoDate: string,
  extras: { center_lat?: number | null; center_lon?: number | null } | null | undefined,
): Geolocation | null {
  const lat = extras?.center_lat;
  const lon = extras?.center_lon;
  if (typeof lat !== 'number' || typeof lon !== 'number') return null;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90) return null;
  const { day, hours, utcOffsetH } = splitIso(isoDate);
  const zoneChanged = Math.abs(utcOffsetH - lon / 15) > MAX_ZONE_DRIFT_H;
  const offset = zoneChanged ? Math.round(lon / 15) : utcOffsetH;
  return {
    lat,
    lon,
    utcOffsetH: offset,
    zoneChanged,
    isoDate: zoneChanged ? composeIso(day, hours, offset) : isoDate,
  };
}
