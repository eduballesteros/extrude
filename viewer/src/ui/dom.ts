/**
 * Utilidades DOM. Todo texto que pueda venir del usuario o del DWG (nombres de capa, vista,
 * archivo) se asigna con textContent; innerHTML solo se usa con los SVG constantes de icons.ts.
 */
import { ICONS, type IconName } from './icons';

type Props<K extends keyof HTMLElementTagNameMap> = Partial<
  Omit<HTMLElementTagNameMap[K], 'style' | 'dataset'>
> & {
  class?: string;
  data?: Record<string, string>;
  attrs?: Record<string, string>;
};

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Props<K> = {},
  ...children: (Node | string | null | undefined | false)[]
): HTMLElementTagNameMap[K] {
  const { class: className, data, attrs, ...rest } = props;
  const el = Object.assign(document.createElement(tag), rest);
  if (className) el.className = className;
  for (const [k, v] of Object.entries(data ?? {})) el.dataset[k] = v;
  for (const [k, v] of Object.entries(attrs ?? {})) el.setAttribute(k, v);
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child);
  }
  return el;
}

/** Icono SVG (constante, nunca datos del usuario). */
export function icon(name: IconName, size = 16): SVGSVGElement {
  const wrapper = document.createElement('span');
  wrapper.innerHTML = ICONS[name];
  const svg = wrapper.firstElementChild as SVGSVGElement;
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon');
  return svg;
}

export interface ButtonOptions {
  label?: string;
  icon?: IconName;
  title?: string;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md';
  onClick?: (event: MouseEvent) => void;
}

export function button(options: ButtonOptions): HTMLButtonElement {
  const { label, title, variant = 'secondary', size = 'md', onClick } = options;
  const el = h('button', {
    type: 'button',
    class: `btn btn-${variant} btn-${size}${label ? '' : ' btn-icon'}`,
  });
  if (title) {
    el.title = title;
    el.setAttribute('aria-label', title);
  }
  if (options.icon) el.append(icon(options.icon));
  if (label) el.append(h('span', { textContent: label }));
  if (onClick) el.addEventListener('click', onClick);
  return el;
}

/** Control segmentado (botones exclusivos). Devuelve el elemento y un setter de valor. */
export function segmented<T extends string>(
  options: { value: T; label: string; icon?: IconName; title?: string }[],
  initial: T,
  onChange: (value: T) => void,
): { element: HTMLElement; set: (value: T) => void } {
  const element = h('div', { class: 'segmented', attrs: { role: 'radiogroup' } });
  const buttons = options.map((opt) => {
    const b = h('button', {
      type: 'button',
      class: 'segmented-item',
      title: opt.title ?? opt.label,
      attrs: { role: 'radio' },
    });
    if (opt.icon) b.append(icon(opt.icon, 14));
    b.append(h('span', { textContent: opt.label }));
    b.addEventListener('click', () => {
      set(opt.value);
      onChange(opt.value);
    });
    element.append(b);
    return { b, value: opt.value };
  });
  const set = (value: T): void => {
    for (const { b, value: v } of buttons) {
      b.classList.toggle('is-active', v === value);
      b.setAttribute('aria-checked', String(v === value));
    }
  };
  set(initial);
  return { element, set };
}

export interface SliderOptions {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  unit?: string;
  digits?: number;
  onInput: (value: number) => void;
}

/**
 * Interpreta lo tecleado en un campo numérico: admite coma decimal y espacios; devuelve null si
 * no es un número finito. El valor se ajusta a [min, max] y al paso.
 */
export function parseNumeric(text: string, min: number, max: number, step: number): number | null {
  const cleaned = text.trim().replace(/\s+/g, '').replace(',', '.');
  if (!/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(cleaned)) return null;
  const raw = Number(cleaned);
  if (!Number.isFinite(raw)) return null;
  const clamped = Math.min(Math.max(raw, min), max);
  const snapped = Math.round((clamped - min) / step) * step + min;
  return Number(Math.min(snapped, max).toFixed(10));
}

export function formatValue(v: number, digits: number): string {
  return v.toFixed(digits);
}

/**
 * Fila CAD: etiqueta a la izquierda (arrastrable para variar el valor), pista fina y valor
 * numérico en monoespaciada a la derecha, editable por teclado con validación (Enter aplica,
 * Esc descarta, ↑/↓ suman un paso; Mayús ×10).
 */
export function slider(options: SliderOptions): { element: HTMLElement; set: (v: number) => void } {
  const { label, min, max, step, unit = '', digits = 2, onInput } = options;
  let current = options.value;
  const range = h('input', {
    type: 'range',
    min: String(min),
    max: String(max),
    step: String(step),
    tabIndex: -1,
  });
  const number = h('input', {
    type: 'text',
    inputMode: 'decimal',
    spellcheck: false,
    class: 'num',
    attrs: { 'aria-label': label },
  });
  const set = (v: number): void => {
    current = v;
    range.value = String(v);
    if (document.activeElement !== number) number.value = formatValue(v, digits);
    number.classList.remove('is-invalid');
    const pct = ((v - min) / (max - min)) * 100;
    range.style.setProperty('--fill', `${Math.min(Math.max(pct, 0), 100)}%`);
  };
  const commit = (v: number): void => {
    set(v);
    onInput(v);
  };
  range.addEventListener('input', () => commit(Number(range.value)));
  const applyText = (): void => {
    const v = parseNumeric(number.value, min, max, step);
    if (v === null) {
      number.classList.add('is-invalid');
      return;
    }
    commit(v);
    number.value = formatValue(v, digits);
  };
  number.addEventListener('change', applyText);
  number.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      applyText();
      number.blur();
    } else if (e.key === 'Escape') {
      number.value = formatValue(current, digits);
      number.classList.remove('is-invalid');
      number.blur();
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault();
      const delta = (e.key === 'ArrowUp' ? step : -step) * (e.shiftKey ? 10 : 1);
      const v = parseNumeric(String(current + delta), min, max, step);
      if (v !== null) {
        commit(v);
        number.value = formatValue(v, digits);
      }
    }
  });
  number.addEventListener('blur', () => {
    if (!number.classList.contains('is-invalid')) number.value = formatValue(current, digits);
  });
  number.addEventListener('focus', () => number.select());

  // Arrastre horizontal sobre la etiqueta (como en Blender/Figma): 1 px = 1 paso
  const labelEl = h('span', { class: 'field-label scrub', textContent: label, title: label });
  labelEl.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startValue = current;
    labelEl.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent): void => {
      const pxPerStep = ev.shiftKey ? 0.2 : 1;
      const v = parseNumeric(
        String(startValue + ((ev.clientX - startX) / pxPerStep) * step),
        min,
        max,
        step,
      );
      if (v !== null && v !== current) commit(v);
    };
    const up = (): void => {
      labelEl.removeEventListener('pointermove', move);
      labelEl.removeEventListener('pointerup', up);
      labelEl.removeEventListener('pointercancel', up);
    };
    labelEl.addEventListener('pointermove', move);
    labelEl.addEventListener('pointerup', up);
    labelEl.addEventListener('pointercancel', up);
  });

  set(options.value);
  const element = h(
    'div',
    { class: 'field field-slider' },
    labelEl,
    range,
    h(
      'span',
      { class: 'num-wrap' },
      number,
      unit ? h('span', { class: 'unit', textContent: unit }) : null,
    ),
  );
  return { element, set };
}

export function toggle(
  label: string,
  value: boolean,
  onChange: (value: boolean) => void,
): { element: HTMLElement; set: (v: boolean) => void } {
  const input = h('input', { type: 'checkbox', class: 'switch-input' });
  input.checked = value;
  input.addEventListener('change', () => onChange(input.checked));
  const element = h(
    'label',
    { class: 'field field-toggle' },
    h('span', { class: 'field-label', textContent: label }),
    h('span', { class: 'switch' }, input, h('span', { class: 'switch-track' })),
  );
  return { element, set: (v) => (input.checked = v) };
}

export function colorField(
  label: string,
  value: string,
  onChange: (value: string) => void,
): { element: HTMLElement; set: (v: string) => void } {
  const input = h('input', { type: 'color', class: 'color-input', value });
  input.addEventListener('input', () => onChange(input.value));
  const element = h(
    'label',
    { class: 'field field-color' },
    h('span', { class: 'field-label', textContent: label }),
    input,
  );
  return { element, set: (v) => (input.value = v) };
}

export function select<T extends string>(
  label: string,
  options: { value: T; label: string }[],
  value: T,
  onChange: (value: T) => void,
): { element: HTMLElement; set: (v: T) => void } {
  const el = h('select', { class: 'select' });
  for (const o of options) el.append(h('option', { value: o.value, textContent: o.label }));
  el.value = value;
  el.addEventListener('change', () => onChange(el.value as T));
  const element = h(
    'label',
    { class: 'field' },
    h('span', { class: 'field-label', textContent: label }),
    el,
  );
  return { element, set: (v) => (el.value = v) };
}

/** Sección plegable con cabecera compacta; `aside` es un nodo opcional a la derecha. */
export function section(
  title: string,
  open = true,
  aside?: Node,
): {
  element: HTMLElement;
  body: HTMLElement;
} {
  const body = h('div', { class: 'section-body' });
  const header = h(
    'button',
    { type: 'button', class: 'section-header', attrs: { 'aria-expanded': String(open) } },
    icon('chevron-down', 12),
    h('span', { class: 'section-title', textContent: title }),
    aside ?? null,
  );
  const element = h('section', { class: `section${open ? '' : ' is-collapsed'}` }, header, body);
  header.addEventListener('click', () => {
    const collapsed = element.classList.toggle('is-collapsed');
    header.setAttribute('aria-expanded', String(!collapsed));
  });
  return { element, body };
}

export function pickFile(accept: string, onFile: (file: File) => void): void {
  const input = h('input', { type: 'file', accept });
  input.addEventListener('change', () => {
    const file = input.files?.[0];
    if (file) onFile(file);
  });
  input.click();
}

/** 1 400 000 → "1.4M", 12 300 → "12.3K" (para lecturas mono compactas). */
export function compactNumber(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(1)}G`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (abs >= 1e4) return `${(value / 1e3).toFixed(1)}K`;
  return String(Math.round(value));
}

export function formatNumber(value: number, digits = 0): string {
  return value.toLocaleString('es-ES', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}
