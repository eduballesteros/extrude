/**
 * Guía de uso: búsqueda local, copia de bloques, tema e índice de lectura.
 * Sin dependencias del visor: la página es estática y se abre en otra pestaña.
 */
import './guide.css';

const THEME_KEY = 'extrude-theme';

function setTheme(theme: 'light' | 'dark'): void {
  document.documentElement.dataset['theme'] = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', theme === 'dark' ? '#07112c' : '#e2e3de');
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Sin almacenamiento: el tema solo dura mientras la página esté abierta
  }
}

document.getElementById('g-theme')?.addEventListener('click', () => {
  setTheme(document.documentElement.dataset['theme'] === 'dark' ? 'light' : 'dark');
});

const sections = [...document.querySelectorAll<HTMLElement>('.g-content section[id]')];
const normalize = (text: string): string =>
  text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('es');
const index = sections.map((section) => {
  const text = (section.textContent ?? '').replace(/\s+/g, ' ').trim();
  return {
    section,
    title:
      section
        .querySelector('h2')
        ?.textContent?.replace(/^\s*\d+\s*/, '')
        .trim() ?? section.id,
    text,
    searchable: normalize(text),
  };
});
const search = document.querySelector<HTMLInputElement>('#g-search');
const results = document.getElementById('g-search-results');
const status = document.getElementById('g-search-status');

function searchGuide(): void {
  if (!search || !results || !status) return;
  const words = normalize(search.value.trim()).split(/\s+/).filter(Boolean);
  results.replaceChildren();
  results.hidden = words.length === 0;
  status.textContent = '';
  if (!words.length) return;
  const matches = index.filter((entry) => words.every((word) => entry.searchable.includes(word)));
  status.textContent = matches.length
    ? `${matches.length} apartados encontrados${matches.length > 6 ? ' · se muestran los primeros 6' : ''}.`
    : 'Sin resultados. Prueba con «altura», «materiales» o «exportar».';
  for (const match of matches.slice(0, 6)) {
    const item = document.createElement('li');
    const link = document.createElement('a');
    link.href = `#${match.section.id}`;
    const title = document.createElement('strong');
    title.textContent = match.title;
    const snippet = document.createElement('span');
    const position = Math.max(0, match.searchable.indexOf(words[0]!) - 32);
    snippet.textContent = `${position ? '…' : ''}${match.text.slice(position, position + 120)}…`;
    link.append(title, snippet);
    link.addEventListener('click', () => {
      // Una coincidencia dentro de una respuesta plegada debe quedar visible al llegar.
      match.section.querySelectorAll('details').forEach((detail) => {
        const text = normalize(detail.textContent ?? '');
        if (words.every((word) => text.includes(word))) detail.open = true;
      });
      match.section.tabIndex = -1;
      match.section.focus({ preventScroll: true });
    });
    item.append(link);
    results.append(item);
  }
}

search?.addEventListener('input', searchGuide);
search?.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    search.value = '';
    searchGuide();
  } else if (event.key === 'Enter') {
    results?.querySelector<HTMLAnchorElement>('a')?.click();
  }
});

// Se copia el texto original: el botón nunca forma parte del comando o YAML.
document.querySelectorAll<HTMLElement>('.g-content pre').forEach((pre) => {
  const code = pre.querySelector('code');
  if (!code) return;
  const text = code.textContent ?? '';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'g-copy';
  button.textContent = 'Copiar';
  button.setAttribute('aria-label', 'Copiar este bloque de texto');
  button.setAttribute('aria-live', 'polite');
  let timer: ReturnType<typeof setTimeout> | undefined;
  button.addEventListener('click', () => {
    void (async () => {
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = 'Copiado';
      } catch {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
        button.textContent = 'Usa Ctrl+C / ⌘C';
      }
      clearTimeout(timer);
      timer = setTimeout(() => (button.textContent = 'Copiar'), 2500);
    })();
  });
  pre.classList.add('g-copyable');
  pre.append(button);
});

// Índice: marca el apartado que se está leyendo
const links = new Map(
  [...document.querySelectorAll<HTMLAnchorElement>('.g-toc ol a')].map((a) => [a.hash.slice(1), a]),
);
const visible = new Set<Element>();
const observer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) visible.add(entry.target);
      else visible.delete(entry.target);
    }
    const current = [...visible].sort(
      (a, b) =>
        Math.abs(a.getBoundingClientRect().top - 80) - Math.abs(b.getBoundingClientRect().top - 80),
    )[0];
    if (!current) return;
    links.forEach((link, id) => {
      link.classList.toggle('is-active', id === current.id);
      if (id === current.id) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  },
  { rootMargin: '-80px 0px -65% 0px' },
);
sections.forEach((section) => observer.observe(section));
