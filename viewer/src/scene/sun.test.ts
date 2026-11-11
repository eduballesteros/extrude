import SunCalc from 'suncalc';
import { describe, expect, it } from 'vitest';
import {
  composeIso,
  geolocate,
  kelvinToRgb,
  localHours,
  splitIso,
  sunAngles,
  sunDirection,
  sunTimes,
} from './sun';

const OBLIQUITY = 23.44;
const MADRID = { lat: 40.4168, lon: -3.7038 };

describe('sunAngles frente a valores astronómicos conocidos', () => {
  it('Madrid, solsticio de verano, mediodía solar (12:16 UTC): elevación 90−φ+ε, azimut sur', () => {
    const angles = sunAngles(new Date('2024-06-21T12:16:24Z'), MADRID.lat, MADRID.lon);
    expect(angles.elevationDeg).toBeCloseTo(90 - MADRID.lat + OBLIQUITY, 0);
    expect(Math.abs(angles.azimuthDeg - 180)).toBeLessThan(1);
  });

  it('Oslo, solsticio de invierno, mediodía solar: elevación 90−φ−ε ≈ 6,6°', () => {
    const lat = 59.9139;
    const angles = sunAngles(new Date('2024-12-21T11:15:00Z'), lat, 10.7522);
    expect(angles.elevationDeg).toBeCloseTo(90 - lat - OBLIQUITY, 0);
    expect(Math.abs(angles.azimuthDeg - 180)).toBeLessThan(2);
  });

  it('Ecuador, equinoccio, mediodía: sol casi en el cénit', () => {
    const angles = sunAngles(new Date('2024-03-20T12:07:30Z'), 0, 0);
    expect(angles.elevationDeg).toBeGreaterThan(89);
  });

  it('equinoccio, amanecer en latitud 40: sale por el este', () => {
    const angles = sunAngles(new Date('2024-03-20T06:07:00Z'), 40, 0);
    expect(Math.abs(angles.azimuthDeg - 90)).toBeLessThan(3);
    expect(Math.abs(angles.elevationDeg)).toBeLessThan(2);
  });

  it('convierte el convenio de suncalc (desde el sur, positivo al oeste) al geográfico', () => {
    const date = new Date('2024-06-21T08:00:00Z');
    const raw = SunCalc.getPosition(date, MADRID.lat, MADRID.lon);
    const angles = sunAngles(date, MADRID.lat, MADRID.lon);
    expect(angles.azimuthDeg).toBeCloseTo((raw.azimuth * 180) / Math.PI + 180, 6);
    expect(angles.elevationDeg).toBeCloseTo((raw.altitude * 180) / Math.PI, 6);
    expect(angles.azimuthDeg).toBeGreaterThan(60); // por la mañana, al este
    expect(angles.azimuthDeg).toBeLessThan(120);
  });
});

describe('sunDirection en la escena (Y-up, norte del dibujo = −Z)', () => {
  it.each([
    [0, [0, 0, -1]],
    [90, [1, 0, 0]],
    [180, [0, 0, 1]],
    [270, [-1, 0, 0]],
  ] as const)('azimut %i° al horizonte', (azimuthDeg, expected) => {
    const v = sunDirection({ azimuthDeg, elevationDeg: 0 });
    expect(v.x).toBeCloseTo(expected[0]);
    expect(v.y).toBeCloseTo(expected[1]);
    expect(v.z).toBeCloseTo(expected[2]);
  });

  it('cénit apunta a +Y', () => {
    expect(sunDirection({ azimuthDeg: 123, elevationDeg: 90 }).y).toBeCloseTo(1);
  });

  it('la rotación del norte gira en sentido horario', () => {
    const v = sunDirection({ azimuthDeg: 0, elevationDeg: 0 }, 90);
    expect(v.x).toBeCloseTo(1);
    expect(v.z).toBeCloseTo(0);
  });

  it('las sombras cambian de lado a lo largo del día', () => {
    const at = (iso: string) => sunDirection(sunAngles(new Date(iso), MADRID.lat, MADRID.lon));
    const morning = at('2024-06-21T08:00:00+02:00');
    const evening = at('2024-06-21T19:00:00+02:00');
    expect(morning.x).toBeGreaterThan(0.3); // sol al este → sombras hacia el oeste
    expect(evening.x).toBeLessThan(-0.3);
    const noon = at('2024-06-21T14:16:00+02:00');
    expect(noon.y).toBeGreaterThan(morning.y);
    expect(noon.z).toBeGreaterThan(0); // al sur (+Z) en el hemisferio norte
  });
});

describe('fechas ISO locales', () => {
  it('compone y descompone con desfase', () => {
    const iso = composeIso('2024-06-21', 10.5, 2);
    expect(iso).toBe('2024-06-21T10:30:00+02:00');
    expect(splitIso(iso)).toEqual({ day: '2024-06-21', hours: 10.5, utcOffsetH: 2 });
    expect(new Date(iso).toISOString()).toBe('2024-06-21T08:30:00.000Z');
  });

  it('desfases negativos, fraccionarios y Z', () => {
    expect(composeIso('2024-01-01', 0, -3.5)).toBe('2024-01-01T00:00:00-03:30');
    expect(splitIso('2024-01-01T00:00:00-03:30').utcOffsetH).toBe(-3.5);
    expect(splitIso('2024-01-01T06:15:00Z')).toEqual({
      day: '2024-01-01',
      hours: 6.25,
      utcOffsetH: 0,
    });
    expect(localHours('2024-01-01T23:59:59+01:00')).toBeCloseTo(24, 2);
  });

  it('rechaza fechas sin hora', () => {
    expect(() => splitIso('2024-01-01')).toThrow();
  });
});

describe('kelvinToRgb', () => {
  it('cálido a 2700 K, neutro a 6600 K, frío a 12000 K', () => {
    const [r1, , b1] = kelvinToRgb(2700);
    expect(r1).toBe(1);
    expect(b1).toBeLessThan(0.7);
    const [r2, g2, b2] = kelvinToRgb(6600);
    expect(Math.min(r2, g2, b2)).toBeGreaterThan(0.95);
    const [r3, , b3] = kelvinToRgb(12000);
    expect(b3).toBe(1);
    expect(r3).toBeLessThan(0.85);
  });
});

describe('geolocate: sol en el centro geográfico del modelo', () => {
  const SUMMER_NOON_MADRID = '2024-06-21T12:00:00+02:00';

  it('sin center_lat/center_lon (o no numéricos) no cambia nada', () => {
    expect(geolocate(SUMMER_NOON_MADRID, null)).toBeNull();
    expect(geolocate(SUMMER_NOON_MADRID, { center_lat: null, center_lon: null })).toBeNull();
    expect(geolocate(SUMMER_NOON_MADRID, { center_lat: 120, center_lon: 0 })).toBeNull();
  });

  it('Valencia y Galicia conservan la hora oficial española (UTC+2 en verano)', () => {
    for (const [lat, lon] of [
      [39.4707, -0.3751],
      [42.88, -8.54],
    ] as const) {
      const place = geolocate(SUMMER_NOON_MADRID, { center_lat: lat, center_lon: lon })!;
      expect(place).toMatchObject({ lat, lon, utcOffsetH: 2, zoneChanged: false });
      expect(place.isoDate).toBe(SUMMER_NOON_MADRID);
    }
  });

  it('Tokio: UTC+2 es imposible allí; pasa a UTC+9 con la misma hora local', () => {
    const place = geolocate(SUMMER_NOON_MADRID, { center_lat: 35.6873, center_lon: 139.7401 })!;
    expect(place.zoneChanged).toBe(true);
    expect(place.utcOffsetH).toBe(9);
    expect(place.isoDate).toBe('2024-06-21T12:00:00+09:00');
  });

  it('con el lugar y la zona corregidos el sol del mediodía local queda alto y al sur', () => {
    for (const [lat, lon] of [
      [35.6873, 139.7401], // Tokio
      [19.4172, -99.1619], // Ciudad de México (UTC−7 aproximado)
    ] as const) {
      const place = geolocate(SUMMER_NOON_MADRID, { center_lat: lat, center_lon: lon })!;
      const { day, utcOffsetH } = splitIso(place.isoDate);
      const times = sunTimes(day, lat, lon, utcOffsetH);
      expect(times.sunrise!).toBeLessThan(12);
      expect(times.sunset!).toBeGreaterThan(12);
      const angles = sunAngles(new Date(place.isoDate), lat, lon);
      expect(angles.elevationDeg).toBeGreaterThan(45);
    }
  });

  it('hemisferio sur (Santiago, invierno austral): mediodía con el sol al norte', () => {
    const place = geolocate(SUMMER_NOON_MADRID, { center_lat: -33.4554, center_lon: -70.657 })!;
    expect(place.utcOffsetH).toBe(-5);
    const angles = sunAngles(new Date(place.isoDate), place.lat, place.lon);
    expect(angles.elevationDeg).toBeGreaterThan(20);
    expect(Math.min(angles.azimuthDeg, 360 - angles.azimuthDeg)).toBeLessThan(60);
  });
});
