/**
 * Tema claro (gris salvia) u oscuro (azul noche). La preferencia se guarda por
 * navegador; sin preferencia guardada se sigue la del sistema. index.html aplica el tema antes de
 * pintar (sin parpadeo); este módulo lo cambia en caliente.
 */

export type Theme = 'light' | 'dark';

export const THEME_KEY = 'extrude-theme';
/** Color de la barra del navegador (meta theme-color), igual que --bg-app. */
export const THEME_COLOR: Record<Theme, string> = { light: '#e2e3de', dark: '#07112c' };

export function isTheme(value: unknown): value is Theme {
  return value === 'light' || value === 'dark';
}

export function currentTheme(): Theme {
  const value = document.documentElement.dataset['theme'];
  return isTheme(value) ? value : 'light';
}

export function applyTheme(theme: Theme, persist = false): void {
  document.documentElement.dataset['theme'] = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[theme]);
  if (!persist) return;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // sin almacenamiento el tema dura lo que la pestaña
  }
}

export function toggleTheme(): Theme {
  const next: Theme = currentTheme() === 'light' ? 'dark' : 'light';
  applyTheme(next, true);
  return next;
}
