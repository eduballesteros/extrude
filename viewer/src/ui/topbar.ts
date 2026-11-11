import type { App } from '../app';
import type { LoadedModel } from '../scene/loader';
import type { CameraType } from '../state';
import type { ViewManager } from '../views/viewManager';
import { button, h, icon, pickFile, segmented } from './dom';
import type { IconName } from './icons';

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
  onOpen: () => void;
  onToggleZen: () => void;
}

/** Barra superior del espacio de trabajo. */
export class TopBar {
  readonly element: HTMLElement;
  private readonly modelName = h('span', { class: 'model-name' });
  private readonly badge = h('span', { class: 'badge', hidden: true });
  private readonly projection: ReturnType<typeof segmented<CameraType>>;
  private readonly menu = h('div', { class: 'menu', hidden: true, attrs: { role: 'menu' } });
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

    const exportButton = button({ label: 'Exportar', icon: 'download', variant: 'primary' });
    exportButton.append(icon('chevron-down', 14));
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
        h(
          'div',
          { class: 'brand' },
          h('span', { class: 'brand-mark' }, icon('logo', 18)),
          h('span', { textContent: 'Extrude' }),
        ),
        h('span', { class: 'divider' }),
        h('div', { class: 'model-info' }, icon('cube', 15), this.modelName, this.badge),
        button({
          label: 'Abrir',
          icon: 'open',
          variant: 'ghost',
          title: 'Abrir o cambiar de modelo',
          onClick: handlers.onOpen,
        }),
      ),
      h(
        'div',
        { class: 'topbar-center' },
        this.projection.element,
        button({
          icon: 'frame',
          title: 'Encuadrar todo (F)',
          variant: 'ghost',
          onClick: () => app.nav.frame(),
        }),
      ),
      h(
        'div',
        { class: 'topbar-right' },
        h('div', { class: 'menu-anchor' }, exportButton, this.menu),
        button({
          icon: 'zen',
          title: 'Ocultar/mostrar paneles (Tab)',
          variant: 'ghost',
          onClick: handlers.onToggleZen,
        }),
      ),
    );
    document.body.append(this.element);
    app.on('state', () => this.projection.set(app.state.camera.type));
  }

  setModel(model: LoadedModel): void {
    this.modelName.textContent = model.name;
    this.modelName.title = model.name;
    const extras = model.extras;
    const offset = extras?.origin_offset;
    const hasOffset = offset && offset.some((v) => Math.abs(v) > 0.001);
    this.badge.hidden = !(extras?.crs || hasOffset);
    this.badge.textContent = extras?.crs ?? (hasOffset ? 'Origen recentrado' : '');
    this.badge.title = offset
      ? `origin_offset: ${offset.map((v) => v.toFixed(2)).join(', ')} m (coordenadas reales = escena + offset)`
      : '';
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
        label: 'Imagen PNG',
        hint: `${views.render.width}×${views.render.height} px · encuadre del marco`,
        run: guard(async () => {
          app.notify('Renderizando PNG…');
          await views.exportPng();
          app.notify('PNG exportado', 'success');
        }),
      },
      {
        icon: 'blender',
        label: 'Script para Blender',
        hint: 'blender_setup.py · cámaras, sol y luces por vista',
        run: guard(() => views.exportBlenderScript()),
      },
      {
        icon: 'json',
        label: 'views.json',
        hint: 'Vistas con coordenadas reales',
        run: guard(() => views.exportViewsJson()),
      },
      {
        icon: 'cube',
        label: 'GLB con cámaras y luces',
        hint: 'KHR_lights_punctual · luces de la vista activa',
        run: guard(() => {
          const skipped = views.exportGlbWithLights();
          if (skipped.length)
            app.notify(`GLB exportado. Omitido: ${skipped.join('; ')}`, 'warning');
        }),
      },
      {
        icon: 'upload',
        label: 'Importar views.json…',
        hint: 'Añadir vistas guardadas',
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
        icon(item.icon),
        h(
          'span',
          { class: 'menu-text' },
          h('span', { textContent: item.label }),
          h('small', { textContent: item.hint }),
        ),
      );
      entry.addEventListener('click', () => {
        this.menu.hidden = true;
        void item.run();
      });
      this.menu.append(entry);
    }
    if (this.downloads.length) {
      this.menu.append(
        h('div', { class: 'menu-label', textContent: 'Resultados de la conversión' }),
      );
      for (const link of this.downloads) {
        this.menu.append(
          h(
            'a',
            {
              class: 'menu-item',
              href: link.href,
              download: link.filename,
              attrs: { role: 'menuitem' },
            },
            icon('file'),
            h(
              'span',
              { class: 'menu-text' },
              h('span', { textContent: link.label }),
              h('small', { textContent: link.filename }),
            ),
          ),
        );
      }
    }
    this.menu.hidden = false;
  }
}
