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

/** Fila con etiqueta, deslizador y valor numérico editable. */
export function slider(options: SliderOptions): { element: HTMLElement; set: (v: number) => void } {
  const { label, min, max, step, unit = '', digits = 2, onInput } = options;
  const range = h('input', {
    type: 'range',
    min: String(min),
    max: String(max),
    step: String(step),
  });
  const number = h('input', {
    type: 'number',
    min: String(min),
    max: String(max),
    step: String(step),
    class: 'num',
  });
  const paint = (v: number): void => {
    const pct = ((v - min) / (max - min)) * 100;
    range.style.setProperty('--fill', `${Math.min(Math.max(pct, 0), 100)}%`);
  };
  const set = (v: number): void => {
    range.value = String(v);
    number.value = v.toFixed(digits).replace(/\.?0+$/, '') || '0';
    paint(v);
  };
  range.addEventListener('input', () => {
    const v = Number(range.value);
    set(v);
    onInput(v);
  });
  number.addEventListener('change', () => {
    const v = Math.min(Math.max(Number(number.value), min), max);
    if (Number.isNaN(v)) return;
    set(v);
    onInput(v);
  });
  set(options.value);
  const element = h(
    'label',
    { class: 'field field-slider' },
    h('span', { class: 'field-label', textContent: label }),
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

/** Sección plegable con cabecera e icono. */
export function section(
  title: string,
  iconName: IconName,
  open = true,
): {
  element: HTMLElement;
  body: HTMLElement;
} {
  const body = h('div', { class: 'section-body' });
  const header = h(
    'button',
    { type: 'button', class: 'section-header', attrs: { 'aria-expanded': String(open) } },
    icon(iconName),
    h('span', { textContent: title }),
    icon('chevron-down', 14),
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

export function formatNumber(value: number, digits = 0): string {
  return value.toLocaleString('es-ES', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}
