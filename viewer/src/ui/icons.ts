/**
 * Iconos de trazo uniforme (24×24, stroke 1.75, currentColor), dibujados para Extrude.
 * Son constantes del código: es el único contenido que se inserta como HTML.
 */

const svg = (body: string): string =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

export const ICONS = {
  logo: svg(
    '<path d="M12 2.5 18.5 6v12L12 21.5 5.5 18V6z"/><path d="M5.5 6 12 9.5 18.5 6"/><path d="M12 9.5v12"/>',
  ),
  open: svg(
    '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1"/><path d="M3 7v10a2 2 0 0 0 2 2h12.5a2 2 0 0 0 1.9-1.4L21.5 11H7.6a2 2 0 0 0-1.9 1.4L3 19"/>',
  ),
  upload: svg(
    '<path d="M12 15V4"/><path d="m7 9 5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  ),
  download: svg(
    '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  ),
  frame: svg(
    '<path d="M4 9V5a1 1 0 0 1 1-1h4"/><path d="M15 4h4a1 1 0 0 1 1 1v4"/><path d="M20 15v4a1 1 0 0 1-1 1h-4"/><path d="M9 20H5a1 1 0 0 1-1-1v-4"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
  ),
  zen: svg(
    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m16 10-2 2 2 2"/>',
  ),
  layers: svg('<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>'),
  views: svg(
    '<path d="M3 8V6a2 2 0 0 1 2-2h2"/><path d="M17 4h2a2 2 0 0 1 2 2v2"/><path d="M21 16v2a2 2 0 0 1-2 2h-2"/><path d="M7 20H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/>',
  ),
  sun: svg(
    '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  ),
  shadow: svg(
    '<circle cx="9" cy="9" r="5"/><path d="M14.5 10.5 21 17"/><path d="M8 14.5 14.5 21"/><path d="M12 13l6 6"/>',
  ),
  globe: svg(
    '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18z"/>',
  ),
  camera: svg(
    '<path d="M4 8a2 2 0 0 1 2-2h1.5l1.5-2h6l1.5 2H18a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><circle cx="12" cy="12.5" r="3.5"/>',
  ),
  bulb: svg(
    '<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z"/>',
  ),
  eye: svg(
    '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  ),
  'eye-off': svg(
    '<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1"/><path d="M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.8 9.8 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  ),
  isolate: svg(
    '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  ),
  focus: svg(
    '<path d="M3 8V5a2 2 0 0 1 2-2h3"/><path d="M16 3h3a2 2 0 0 1 2 2v3"/><path d="M21 16v3a2 2 0 0 1-2 2h-3"/><path d="M8 21H5a2 2 0 0 1-2-2v-3"/><circle cx="12" cy="12" r="1.5"/>',
  ),
  play: svg(
    '<path d="M7 4.5v15a.5.5 0 0 0 .8.4l11.5-7.5a.5.5 0 0 0 0-.8L7.8 4.1a.5.5 0 0 0-.8.4z"/>',
  ),
  pause: svg(
    '<rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/>',
  ),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  copy: svg(
    '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  ),
  'arrow-up': svg('<path d="M12 19V5"/><path d="m6 11 6-6 6 6"/>'),
  'arrow-down': svg('<path d="M12 5v14"/><path d="m6 13 6 6 6-6"/>'),
  trash: svg(
    '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  ),
  refresh: svg(
    '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/>',
  ),
  moon: svg('<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>'),
  close: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
  ruler: svg(
    '<path d="M3.5 15.5 15.5 3.5l5 5-12 12z"/><path d="m7 12 1.5 1.5"/><path d="m9.5 9.5 2 2"/><path d="m12 7 1.5 1.5"/>',
  ),
  'chevron-down': svg('<path d="m6 9 6 6 6-6"/>'),
  check: svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  alert: svg('<path d="M12 3 2.5 20h19z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/>'),
  info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8v.01"/>'),
  image: svg(
    '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  ),
  blender: svg(
    '<path d="M5 14a7 7 0 1 0 7-7"/><circle cx="12" cy="14" r="2.5"/><path d="M12 7 3 3l4 6"/>',
  ),
  json: svg(
    '<path d="M8 4H7a2 2 0 0 0-2 2v4l-2 2 2 2v4a2 2 0 0 0 2 2h1"/><path d="M16 4h1a2 2 0 0 1 2 2v4l2 2-2 2v4a2 2 0 0 1-2 2h-1"/>',
  ),
  cube: svg(
    '<path d="M12 2.5 20.5 7v10L12 21.5 3.5 17V7z"/><path d="M3.5 7 12 11.5 20.5 7"/><path d="M12 11.5v10"/>',
  ),
  file: svg(
    '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  ),
  settings: svg(
    '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  ),
  palette: svg(
    '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.7-.9 1.4-1.9l-.4-1.2a1.5 1.5 0 0 1 1.4-2H17a4 4 0 0 0 4-4c0-4.9-4-8.9-9-8.9z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10.5" cy="7.5" r="1"/><circle cx="15" cy="8" r="1"/>',
  ),
  sky: svg('<path d="M6.5 18a4.5 4.5 0 1 1 .9-8.9A6 6 0 0 1 19 11a3.5 3.5 0 0 1-.5 7z"/>'),
  sunrise: svg(
    '<path d="M12 3v4"/><path d="m8 6 4-3 4 3"/><path d="M4 21h16"/><path d="M6 17a6 6 0 0 1 12 0"/>',
  ),
  sunset: svg(
    '<path d="M12 7V3"/><path d="m8 4 4 3 4-3"/><path d="M4 21h16"/><path d="M6 17a6 6 0 0 1 12 0"/>',
  ),
  stats: svg('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
  keyboard: svg(
    '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  ),
} as const;

export type IconName = keyof typeof ICONS;
