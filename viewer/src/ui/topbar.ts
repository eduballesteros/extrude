import { downloadWithSession, sessionToken } from '../auth/session';
import type { App } from '../app';
import type { LoadedModel, SceneExtras } from '../scene/loader';
import type { CameraType } from '../state';
import type { ViewManager } from '../views/viewManager';
import { button, h, icon, pickFile, segmented } from './dom';
import type { IconName } from './icons';
import { currentTheme, toggleTheme } from './theme';

export interface DownloadLink {
  label: string;
  href: string;
  filename: string;
}

interface MenuItem {
  icon: IconName;
  label: string;
  hint: string;
  run: () => void | Promise<void>;
}

export interface TopBarHandlers {
  onClose: () => void;
  onToggleZen: () => void;
  /** Abre la biblioteca de vistas e imágenes del proyecto. */
  onOpenLibrary: () => void;
}

/**
 * Texto del distintivo de georreferencia: CRS (o "Recentrado") y el desplazamiento X, Y en
 * metros (ejes del DWG). null si el modelo no está desplazado ni tiene CRS.
 */
export function originBadge(extras: SceneExtras | null | undefined): string | null {
  // Solo el sistema de referencia: el desplazamiento de origen es un detalle técnico y va en el
  // título emergente de la etiqueta y del nombre del modelo.
  return extras?.crs ?? null;
}

/** Barra superior del espacio de trabajo (36 px). */
/** Guía de uso: página aparte (guia.html), en otra pestaña para no perder el modelo abierto. */
export function openGuide(): void {
  window.open(new URL('guia.html', document.baseURI).toString(), '_blank', 'noopener');
}

export class TopBar {
  readonly element: HTMLElement;
  private readonly modelName = h('span', { class: 'model-name' });
  private readonly badge = h('span', { class: 'badge mono', hidden: true });
  private readonly projection: ReturnType<typeof segmented<CameraType>>;
  private readonly menu = h('div', { class: 'menu', hidden: true, attrs: { role: 'menu' } });
  private readonly measureButton: HTMLButtonElement;
  private readonly themeButton: HTMLButtonElement;
  private readonly libraryCount = h('span', { class: 'tab-count' });
  private downloads: DownloadLink[] = [];

  constructor(
    private readonly app: App,
    private readonly views: ViewManager,
    handlers: TopBarHandlers,
  ) {
    this.projection = segmented<CameraType>(
      [
        { value: 'perspective', label: 'Perspectiva' },
        { value: 'orthographic', label: 'Ortográfica' },
      ],
      app.state.camera.type,
      (type) => {
        app.state.camera.type = type;
        app.applyCamera();
      },
    );

    this.measureButton = button({
      label: 'Medir',
      icon: 'ruler',
      title: 'Medir distancias y desniveles (M) · Esc borra',
      variant: 'ghost',
      size: 'sm',
      onClick: () => app.setMeasuring(!app.measure.active),
    });
    this.measureButton.setAttribute('aria-pressed', 'false');

    this.themeButton = button({
      icon: currentTheme() === 'dark' ? 'sun' : 'moon',
      title: 'Cambiar entre tema claro y oscuro',
      variant: 'ghost',
      size: 'sm',
      onClick: () => {
        const theme = toggleTheme();
        this.themeButton.replaceChildren(icon(theme === 'dark' ? 'sun' : 'moon'));
      },
    });

    const exportButton = button({ label: 'Exportar', variant: 'primary', size: 'sm' });
    exportButton.append(icon('chevron-down', 12));
    exportButton.setAttribute('aria-haspopup', 'menu');
    exportButton.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleMenu();
    });
    document.addEventListener('click', () => (this.menu.hidden = true));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.menu.hidden = true;
    });

    this.element = h(
      'header',
      { class: 'topbar' },
      h(
        'div',
        { class: 'topbar-left' },
        h('span', { class: 'brand-mark', title: 'Extrude' }, icon('logo', 14)),
        h('span', { class: 'divider' }),
        this.modelName,
        this.badge,
      ),
      h(
        'div',
        { class: 'topbar-center' },
        this.projection.element,
        button({
          label: 'Ajustar',
          icon: 'frame',
          title: 'Encuadrar todo (F)',
          variant: 'ghost',
          size: 'sm',
          onClick: () => app.nav.frame(),
        }),
        this.measureButton,
      ),
      h(
        'div',
        { class: 'topbar-right' },
        this.libraryButton(handlers),
        button({
          label: 'Guía',
          icon: 'info',
          title: 'Guía de uso (se abre en otra pestaña)',
          variant: 'ghost',
          size: 'sm',
          onClick: () => openGuide(),
        }),
        this.themeButton,
        button({
          icon: 'zen',
          title: 'Ocultar/mostrar paneles (Tab)',
          variant: 'ghost',
          size: 'sm',
          onClick: handlers.onToggleZen,
        }),
        h('div', { class: 'menu-anchor' }, exportButton, this.menu),
        h('span', { class: 'divider' }),
        button({
          label: 'Cerrar modelo',
          icon: 'close',
          title: 'Cerrar y volver al inicio',
          variant: 'ghost',
          size: 'sm',
          onClick: handlers.onClose,
        }),
      ),
    );
    document.body.append(this.element);
    app.on('state', () => {
      this.projection.set(app.state.camera.type);
      this.measureButton.classList.toggle('is-active', app.measure.active);
      this.measureButton.setAttribute('aria-pressed', String(app.measure.active));
    });
  }

  private libraryButton(handlers: TopBarHandlers): HTMLButtonElement {
    const el = button({
      label: 'Biblioteca',
      icon: 'image',
      title: 'Biblioteca de vistas e imágenes del proyecto: capturas del visor y generadas por IA',
      variant: 'ghost',
      size: 'sm',
      onClick: handlers.onOpenLibrary,
    });
    el.classList.add('library-button');
    el.append(this.libraryCount);
    return el;
  }

  /** Número de imágenes de la biblioteca del proyecto (junto al botón). */
  setLibraryCount(count: number): void {
    this.libraryCount.textContent = count ? String(count) : '';
  }

  setModel(model: LoadedModel): void {
    this.modelName.textContent = model.name;
    this.modelName.title = model.name;
    const text = originBadge(model.extras);
    this.badge.hidden = text === null;
    this.badge.textContent = text ?? '';
    const offset = model.extras?.origin_offset;
    this.badge.title = offset
      ? `origin_offset (m, ejes DWG): ${offset.map((v) => v.toFixed(3)).join(', ')} · coordenadas reales = escena + offset`
      : '';
    if (offset && offset.some((v) => Math.abs(v) > 0.001)) {
      const [x, y] = offset;
      this.modelName.title = `${model.name}\nRecentrado en ${x.toFixed(0)}, ${y.toFixed(0)} m (coordenadas del dibujo)`;
    }
    this.projection.set(this.app.state.camera.type);
  }

  setDownloads(links: DownloadLink[]): void {
    this.downloads = links;
  }

  private items(): MenuItem[] {
    const { views, app } = this;
    const guard = (fn: () => unknown) => (): void => {
      try {
        const result = fn();
        if (result instanceof Promise)
          result.catch((e: unknown) =>
            app.notify(String(e instanceof Error ? e.message : e), 'error'),
          );
      } catch (error) {
        app.notify(String(error instanceof Error ? error.message : error), 'error');
      }
    };
    return [
      {
        icon: 'image',
        label: 'PNG',
        hint: `${views.render.width}×${views.render.height}`,
        run: guard(async () => {
          views.assertExportable(); // antes del aviso: sin «Renderizando» si no se puede
          app.notify('Renderizando PNG…');
          await views.exportPng();
          app.notify('PNG exportado', 'success');
        }),
      },
      {
        icon: 'blender',
        label: 'Script Blender',
        hint: 'blender_setup.py',
        run: guard(() => views.exportBlenderScript()),
      },
      {
        icon: 'json',
        label: 'views.json',
        hint: 'coord. reales',
        run: guard(() => views.exportViewsJson()),
      },
      {
        icon: 'cube',
        label: 'GLB + cámaras y luces',
        hint: 'KHR_lights',
        run: guard(() => {
          const skipped = views.exportGlbWithLights();
          if (skipped.length)
            app.notify(`GLB exportado. Omitido: ${skipped.join('; ')}`, 'warning');
        }),
      },
      {
        icon: 'upload',
        label: 'Importar views.json…',
        hint: '',
        run: () =>
          pickFile('.json,application/json', (file) => {
            views
              .importViewsJson(file)
              .then((n) => app.notify(`${n} vistas importadas`, 'success'))
              .catch((e: unknown) => app.notify(`views.json no válido: ${String(e)}`, 'error'));
          }),
      },
    ];
  }

  private menuEntry(iconName: IconName, label: string, hint: string): Element[] {
    return [
      icon(iconName, 14),
      h('span', { class: 'menu-label-text', textContent: label }),
      h('span', { class: 'menu-hint', textContent: hint }),
    ];
  }

  private toggleMenu(): void {
    if (!this.menu.hidden) {
      this.menu.hidden = true;
      return;
    }
    this.menu.replaceChildren();
    for (const item of this.items()) {
      const entry = h(
        'button',
        { type: 'button', class: 'menu-item', attrs: { role: 'menuitem' } },
        ...this.menuEntry(item.icon, item.label, item.hint),
      );
      entry.addEventListener('click', () => {
        this.menu.hidden = true;
        void item.run();
      });
      this.menu.append(entry);
    }
    if (this.downloads.length) {
      this.menu.append(h('div', { class: 'menu-section', textContent: 'Conversión' }));
      for (const link of this.downloads) {
        const anchor = h(
          'a',
          {
            class: 'menu-item',
            href: link.href,
            download: link.filename,
            attrs: { role: 'menuitem' },
          },
          ...this.menuEntry('file', link.label, link.filename),
        );
        // Servidor compartido: el enlace no lleva la sesión; se descarga con fetch
        anchor.addEventListener('click', (e) => {
          if (!sessionToken() || !link.href.startsWith('http')) return;
          e.preventDefault();
          this.menu.hidden = true;
          downloadWithSession(link.href, link.filename).catch((err: unknown) =>
            this.app.notify(err instanceof Error ? err.message : String(err), 'error'),
          );
        });
        this.menu.append(anchor);
      }
    }
    this.menu.hidden = false;
  }
}
