/**
 * Pestaña Materiales (Fase 2, docs/REQUISITOS_MATERIALIDAD_IA.md): asignación manual por objeto y
 * superficie, previsualización, deshacer y confirmación, sin IA.
 *
 * La materialización la hace siempre `extrude serve` con el mismo código que la CLI, así que lo
 * que se ve aquí es exactamente lo que se exporta. La previsualización recarga el GLB del servidor
 * conservando cámara, sol y capas; descartar vuelve al último GLB confirmado.
 */
import type { App } from '../app';
import { ConversionError } from '../convert/client';
import {
  buildCatalog,
  sortedObjects,
  SURFACE_KEYS,
  SURFACE_NAMES,
  type CatalogObject,
  type SurfaceKey,
} from '../materials/catalog';
import {
  MaterialsClient,
  PlanRejectedError,
  type Library,
  type LibraryMaterial,
  type PlanIssue,
} from '../materials/client';
import { PlanEditor, type Assignment, type MaterialPlan } from '../materials/plan';
import { button, h, pickFile, section } from './dom';

const STORAGE_PREFIX = 'extrude.materials.';
const SUMMARY_LABELS = 3;

type Mode = 'idle' | 'busy';

export interface MaterialsPanelOptions {
  server: string;
  /** Lanza si el usuario no ha permitido enviar archivos a ese servidor. */
  authorizeUpload: () => void;
}

function storageGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Almacenamiento no disponible (privado, cuota): el borrador solo vive en la sesión
  }
}

function numberInput(value: number, min: number, max: number, step: number): HTMLInputElement {
  return h('input', {
    type: 'number',
    class: 'input input-number',
    value: String(value),
    min: String(min),
    max: String(max),
    step: String(step),
  });
}

export function summarizeLabels(labels: string[]): string {
  if (labels.length <= SUMMARY_LABELS) return labels.join(', ');
  return `${labels.slice(0, SUMMARY_LABELS).join(', ')} +${labels.length - SUMMARY_LABELS}`;
}

export class MaterialsPanel {
  readonly element = h('div', { class: 'tab-panel materials-panel' });
  private readonly client: MaterialsClient;
  private readonly editor = new PlanEditor();
  private catalog = new Map<string, CatalogObject>();
  private library: Library | null = null;
  private readonly selected = new Set<string>();
  private job: string | null = null;
  private geometryRevision: string | null = null;
  private confirmedRevision: string | null = null;
  /** GLB a restaurar al descartar: el último confirmado o el base. */
  private confirmedUrl: string | null = null;
  private previewing = false;
  private reloading = false;
  private mode: Mode = 'idle';
  private issues: PlanIssue[] = [];
  private downloads: { label: string; href: string; filename: string }[] = [];

  private readonly status = h('div', { class: 'mat-status', attrs: { role: 'status' } });
  private readonly search = h('input', {
    type: 'search',
    class: 'input search',
    placeholder: 'Filtrar objetos (E-001, capa…)',
    spellcheck: false,
  });
  private readonly objectList = h('ul', { class: 'mat-objects', attrs: { role: 'listbox' } });
  private readonly selectionInfo = h('span', { class: 'mono' });
  private readonly surfaceSelect = h('select', { class: 'select' });
  private readonly materialSelect = h('select', { class: 'select' });
  private readonly colorInput = h('input', { type: 'color', class: 'color-input' });
  private readonly roughnessInput = numberInput(0.8, 0, 1, 0.05);
  private readonly tileWidth = numberInput(2, 0.05, 100, 0.05);
  private readonly tileHeight = numberInput(2, 0.05, 100, 0.05);
  private readonly rotationInput = numberInput(0, -360, 360, 5);
  private readonly assignmentList = h('ul', { class: 'mat-assignments' });
  private readonly issueList = h('ul', { class: 'mat-issues', attrs: { 'aria-live': 'polite' } });
  private readonly downloadList = h('div', { class: 'mat-downloads' });
  private readonly undoButton = button({
    label: 'Deshacer',
    title: 'Deshacer (Ctrl+Z)',
    variant: 'ghost',
    size: 'sm',
    onClick: () => this.editor.undo(),
  });
  private readonly redoButton = button({
    label: 'Rehacer',
    title: 'Rehacer (Ctrl+Y)',
    variant: 'ghost',
    size: 'sm',
    onClick: () => this.editor.redo(),
  });
  private readonly previewButton = button({
    label: 'Previsualizar',
    icon: 'eye',
    variant: 'secondary',
    onClick: () => void this.apply(true),
  });
  private readonly discardButton = button({
    label: 'Descartar',
    variant: 'ghost',
    onClick: () => void this.discard(),
  });
  private readonly confirmButton = button({
    label: 'Confirmar',
    icon: 'check',
    variant: 'primary',
    onClick: () => void this.apply(false),
  });

  constructor(
    private readonly app: App,
    private readonly options: MaterialsPanelOptions,
  ) {
    this.client = new MaterialsClient(options.server);

    const objects = section('Objetos', true, this.selectionInfo);
    objects.body.append(
      this.search,
      h(
        'div',
        { class: 'mat-row-actions' },
        button({
          label: '+ Pulsado en 3D',
          title: 'Marcar el objeto pulsado en la vista',
          variant: 'ghost',
          size: 'sm',
          onClick: () => this.addPicked(),
        }),
        button({
          label: 'Marcar filtrados',
          variant: 'ghost',
          size: 'sm',
          onClick: () => this.selectFiltered(true),
        }),
        button({
          label: 'Ninguno',
          variant: 'ghost',
          size: 'sm',
          onClick: () => this.selectFiltered(false),
        }),
      ),
      this.objectList,
    );

    const assign = section('Asignar material');
    const tile = h(
      'span',
      { class: 'mat-inline' },
      this.tileWidth,
      h('span', { textContent: '×' }),
      this.tileHeight,
    );
    assign.body.append(
      this.field('Superficie', this.surfaceSelect),
      this.field('Material', this.materialSelect),
      this.field('Color', this.colorInput),
      this.field('Rugosidad', this.roughnessInput),
      this.field('Pieza (m)', tile),
      this.field('Giro (°)', this.rotationInput),
      h(
        'div',
        { class: 'mat-row-actions' },
        button({
          label: 'Añadir asignación',
          icon: 'plus',
          variant: 'secondary',
          onClick: () => this.addAssignment(),
        }),
      ),
    );

    const assignments = section('Asignaciones');
    assignments.body.append(
      h('div', { class: 'mat-row-actions' }, this.undoButton, this.redoButton),
      this.assignmentList,
      this.issueList,
      h(
        'div',
        { class: 'mat-row-actions' },
        button({
          label: 'Importar plan',
          icon: 'upload',
          variant: 'ghost',
          size: 'sm',
          onClick: () => pickFile('.json,application/json', (f) => void this.importPlan(f)),
        }),
        button({
          label: 'Exportar plan',
          icon: 'json',
          variant: 'ghost',
          size: 'sm',
          onClick: () => this.exportPlan(),
        }),
      ),
    );

    this.element.append(
      this.status,
      objects.element,
      assign.element,
      assignments.element,
      h('div', { class: 'mat-footer' }, this.discardButton, this.previewButton, this.confirmButton),
      this.downloadList,
    );

    this.search.addEventListener('input', () => this.renderObjects());
    this.materialSelect.addEventListener('change', () => this.applyPresetDefaults());
    this.editor.onChange(() => {
      this.issues = [];
      this.persistDraft();
      this.render();
    });
    this.element.addEventListener('keydown', (event) => {
      if (!(event.ctrlKey || event.metaKey) || event.target instanceof HTMLInputElement) return;
      if (event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) this.editor.redo();
        else this.editor.undo();
      } else if (event.key.toLowerCase() === 'y') {
        event.preventDefault();
        this.editor.redo();
      }
    });
    app.on('model', () => this.onModel());
    app.on('selection', () => this.renderObjects());
    this.render();
  }

  get count(): number {
    return this.editor.assignments.length;
  }

  private field(label: string, control: HTMLElement): HTMLElement {
    return h(
      'label',
      { class: 'field' },
      h('span', { class: 'field-label', textContent: label }),
      control,
    );
  }

  /** El modelo actual procede de este trabajo del servidor (conversión desde el visor). */
  setJob(job: string | null, baseUrl: string | null): void {
    this.job = job;
    this.confirmedUrl = baseUrl;
    if (job) void this.loadConfirmed(job);
    this.render();
  }

  // --- Modelo ---------------------------------------------------------------------------------

  private onModel(): void {
    if (this.reloading) {
      this.catalog = this.app.model
        ? buildCatalog(this.app.model.root)
        : new Map<string, CatalogObject>();
      this.render();
      return;
    }
    const model = this.app.model;
    this.job = null;
    this.confirmedUrl = null;
    this.confirmedRevision = null;
    this.previewing = false;
    this.selected.clear();
    this.downloads = [];
    this.catalog = model ? buildCatalog(model.root) : new Map<string, CatalogObject>();
    this.geometryRevision = model?.extras?.geometry_revision ?? null;
    const draft = this.geometryRevision ? storageGet(STORAGE_PREFIX + this.geometryRevision) : null;
    this.editor.setConfirmed([], true);
    if (draft) {
      try {
        this.editor.replace(JSON.parse(draft) as Assignment[]);
      } catch {
        // Borrador ilegible: se ignora
      }
    }
    if (model && !this.library) void this.loadLibrary();
    this.render();
  }

  private async loadLibrary(): Promise<void> {
    try {
      this.library = await this.client.library();
      this.fillMaterials();
    } catch {
      this.library = null;
    }
    this.render();
  }

  private async loadConfirmed(job: string): Promise<void> {
    const plan = await this.client.confirmedPlan(job);
    if (!plan || job !== this.job) return;
    this.confirmedRevision = plan.materialRevision;
    this.editor.setConfirmed(plan.assignments, !this.editor.dirty);
  }

  private persistDraft(): void {
    if (this.geometryRevision) {
      storageSet(STORAGE_PREFIX + this.geometryRevision, JSON.stringify(this.editor.assignments));
    }
  }

  private get materialized(): boolean {
    return Boolean(this.app.model?.extras?.material_revision);
  }

  // --- Objetos --------------------------------------------------------------------------------

  private filtered(): CatalogObject[] {
    const query = this.search.value.trim().toLowerCase();
    return sortedObjects(this.catalog).filter(
      (o) =>
        !query ||
        o.label.toLowerCase().includes(query) ||
        o.layer.toLowerCase().includes(query) ||
        o.id.includes(query),
    );
  }

  private selectFiltered(on: boolean): void {
    for (const o of this.filtered()) {
      if (on) this.selected.add(o.id);
      else this.selected.delete(o.id);
    }
    this.render();
  }

  private addPicked(): void {
    const id = this.app.selectedObjectId;
    if (!id || !this.catalog.has(id)) {
      this.app.notify('Pulsa antes un edificio en la vista 3D.', 'info');
      return;
    }
    this.selected.add(id);
    this.render();
  }

  private renderObjects(): void {
    const picked = this.app.selectedObjectId;
    const shown = this.filtered();
    this.objectList.replaceChildren(
      ...shown.map((o) => {
        const box = h('input', { type: 'checkbox', checked: this.selected.has(o.id) });
        box.addEventListener('change', () => {
          if (box.checked) this.selected.add(o.id);
          else this.selected.delete(o.id);
          this.render();
        });
        return h(
          'li',
          {
            class: `mat-object${o.id === picked ? ' is-picked' : ''}`,
            title: `${o.id} · ${[...o.surfaces].map((s) => SURFACE_NAMES[s]).join(', ')}`,
          },
          h('label', {}, box, h('span', { class: 'mono', textContent: o.label })),
          h('span', { class: 'mat-layer', textContent: o.layer }),
        );
      }),
    );
    if (!shown.length) {
      this.objectList.append(
        h('li', {
          class: 'empty',
          textContent: this.catalog.size ? 'Sin coincidencias' : 'Sin objetos materializables',
        }),
      );
    }
    this.selectionInfo.textContent = `${this.selected.size} marcados`;
  }

  // --- Asignar --------------------------------------------------------------------------------

  private fillMaterials(): void {
    const current = this.materialSelect.value;
    this.materialSelect.replaceChildren(
      ...(this.library?.materials ?? []).map((m) =>
        h('option', { value: m.id, textContent: `${m.name} (${m.id})` }),
      ),
    );
    if (current) this.materialSelect.value = current;
    this.applyPresetDefaults();
  }

  private preset(id = this.materialSelect.value): LibraryMaterial | undefined {
    return this.library?.materials.find((m) => m.id === id);
  }

  private applyPresetDefaults(): void {
    const preset = this.preset();
    if (!preset) return;
    this.colorInput.value = preset.color;
    this.roughnessInput.value = String(preset.roughness);
    this.tileWidth.value = String(preset.tileSizeM[0]);
    this.tileHeight.value = String(preset.tileSizeM[1]);
    this.rotationInput.value = '0';
  }

  private fillSurfaces(): void {
    const counts = new Map<SurfaceKey, number>();
    for (const id of this.selected) {
      for (const s of this.catalog.get(id)?.surfaces ?? []) counts.set(s, (counts.get(s) ?? 0) + 1);
    }
    const current = this.surfaceSelect.value;
    const available = SURFACE_KEYS.filter((s) => counts.has(s));
    this.surfaceSelect.replaceChildren(
      ...available.map((s) =>
        h('option', {
          value: s,
          textContent:
            counts.get(s) === this.selected.size
              ? SURFACE_NAMES[s]
              : `${SURFACE_NAMES[s]} (${counts.get(s)} de ${this.selected.size})`,
        }),
      ),
    );
    if (available.includes(current as SurfaceKey)) this.surfaceSelect.value = current;
  }

  private readNumber(input: HTMLInputElement, min: number, max: number): number | null {
    const value = Number(input.value);
    return Number.isFinite(value) && value >= min && value <= max ? value : null;
  }

  private addAssignment(): void {
    const preset = this.preset();
    const surface = this.surfaceSelect.value as SurfaceKey;
    if (!preset || !surface) {
      this.app.notify('Marca objetos con esa superficie y elige un material.', 'warning');
      return;
    }
    const ids = [...this.selected].filter((id) => this.catalog.get(id)?.surfaces.has(surface));
    const roughness = this.readNumber(this.roughnessInput, 0, 1);
    const width = this.readNumber(this.tileWidth, 0.01, 100);
    const height = this.readNumber(this.tileHeight, 0.01, 100);
    const rotation = this.readNumber(this.rotationInput, -360, 360);
    if (roughness === null || width === null || height === null || rotation === null) {
      this.app.notify(
        'Revisa los valores: rugosidad 0–1, pieza 0,01–100 m, giro ±360°.',
        'warning',
      );
      return;
    }
    const parameters: Assignment['parameters'] = {};
    if (this.colorInput.value.toLowerCase() !== preset.color.toLowerCase()) {
      parameters.color = this.colorInput.value;
    }
    if (roughness !== preset.roughness) parameters.roughness = roughness;
    if (width !== preset.tileSizeM[0] || height !== preset.tileSizeM[1]) {
      parameters.tileSizeM = [width, height];
    }
    if (rotation !== 0) parameters.rotationDeg = rotation;
    const skipped = this.selected.size - ids.length;
    this.editor.add({
      objectIds: ids,
      surface,
      materialId: preset.id,
      materialVersion: preset.version,
      parameters,
      origin: 'manual',
    });
    if (skipped > 0) {
      this.app.notify(`${skipped} objetos no tienen ${SURFACE_NAMES[surface]}: se omiten.`, 'info');
    }
  }

  private renderAssignments(): void {
    const issuesByRow = new Map<number, PlanIssue[]>();
    for (const issue of this.issues) {
      if (issue.assignment === null) continue;
      issuesByRow.set(issue.assignment, [...(issuesByRow.get(issue.assignment) ?? []), issue]);
    }
    this.assignmentList.replaceChildren(
      ...this.editor.assignments.map((a, index) => {
        const preset = this.preset(a.materialId);
        const color = a.parameters.color ?? preset?.color ?? '#cccccc';
        const labels = a.objectIds.map((id) => this.catalog.get(id)?.label ?? id);
        const extras = [
          a.parameters.tileSizeM ? `${a.parameters.tileSizeM.join('×')} m` : null,
          a.parameters.rotationDeg ? `${a.parameters.rotationDeg}°` : null,
          a.parameters.roughness !== undefined ? `R ${a.parameters.roughness}` : null,
        ].filter(Boolean);
        const remove = button({
          icon: 'trash',
          title: 'Quitar asignación',
          variant: 'ghost',
          size: 'sm',
          onClick: () => this.editor.remove(index),
        });
        const swatch = h('span', { class: 'mat-swatch' });
        swatch.style.background = color;
        const problems = issuesByRow.get(index) ?? [];
        return h(
          'li',
          { class: `mat-assignment${problems.length ? ' has-issue' : ''}` },
          swatch,
          h(
            'div',
            { class: 'mat-assignment-text' },
            h('strong', { textContent: preset?.name ?? a.materialId }),
            h('span', {
              textContent: `${SURFACE_NAMES[a.surface]} · ${summarizeLabels(labels)}`,
              title: labels.join(', '),
            }),
            extras.length ? h('span', { class: 'mono', textContent: extras.join(' · ') }) : null,
            ...problems.map((p) =>
              h('span', { class: 'mat-issue-inline', textContent: p.message }),
            ),
          ),
          remove,
        );
      }),
    );
    if (!this.editor.assignments.length) {
      this.assignmentList.append(
        h('li', {
          class: 'empty',
          textContent: 'Sin asignaciones: marca objetos y añade un material.',
        }),
      );
    }
    const general = this.issues.filter((i) => i.assignment === null);
    this.issueList.replaceChildren(
      ...general.map((i) => h('li', { class: 'mat-issue', textContent: i.message })),
    );
  }

  // --- Aplicar --------------------------------------------------------------------------------

  private async ensureJob(): Promise<string> {
    if (this.job) return this.job;
    const bytes = this.app.model?.bytes;
    if (!bytes) {
      throw new ConversionError(
        'Este modelo no es un GLB único.',
        'Los conjuntos teselados no se materializan: convierte sin teselas.',
      );
    }
    this.options.authorizeUpload();
    this.job = await this.client.uploadModel(bytes);
    this.confirmedUrl = `/api/jobs/${this.job}/model.glb`;
    return this.job;
  }

  private plan(): MaterialPlan {
    return this.editor.toPlan({
      projectId: 'visor',
      geometryRevision: this.geometryRevision ?? '',
      materialRevision: this.confirmedRevision ?? 'mat-000',
      catalogVersion: this.library?.version ?? '',
    });
  }

  private async reload(url: string): Promise<void> {
    const model = this.app.model;
    if (!model) return;
    this.reloading = true;
    try {
      await this.app.load(this.client.url(url), model.name, undefined, undefined, {
        keepView: true,
      });
    } finally {
      this.reloading = false;
    }
  }

  /** Ignora de forma explícita los colores de capa: teñirían las texturas (CA-09). */
  private clearLayerColors(): void {
    const tinted = Object.values(this.app.state.layers).filter((l) => l.colorOverride);
    if (!tinted.length) return;
    for (const layer of tinted) delete layer.colorOverride;
    this.app.applyLayers();
    this.app.notify(
      `Se quitan ${tinted.length} colores de capa para ver los materiales sin teñir.`,
      'info',
    );
  }

  private async apply(preview: boolean): Promise<void> {
    if (this.mode === 'busy') return;
    this.mode = 'busy';
    this.issues = [];
    this.render();
    try {
      const job = await this.ensureJob();
      const result = await this.client.applyPlan(job, this.plan(), preview);
      this.clearLayerColors();
      await this.reload(result.url);
      if (preview) {
        this.previewing = true;
        this.app.notify(
          `Previsualización: ${result.stats.materials} materiales, sin confirmar.`,
          'info',
        );
      } else {
        this.previewing = false;
        this.confirmedUrl = result.url;
        this.confirmedRevision = result.materialRevision;
        this.editor.setConfirmed(this.editor.assignments as Assignment[]);
        const base = (this.app.model?.name ?? 'modelo').replace(/\.glb$/i, '');
        this.downloads = [
          {
            label: 'GLB materializado',
            href: this.client.url(result.url),
            filename: `${base}.materialized.glb`,
          },
          ...(result.planUrl
            ? [
                {
                  label: `materials.json (${result.materialRevision})`,
                  href: this.client.url(result.planUrl),
                  filename: `${base}.materials.json`,
                },
              ]
            : []),
        ];
        this.app.notify(`Materiales confirmados (${result.materialRevision}).`, 'success');
      }
    } catch (error) {
      if (error instanceof PlanRejectedError) this.issues = error.issues;
      const message =
        error instanceof ConversionError ? `${error.message} ${error.fix}` : String(error);
      this.app.notify(message, 'error');
    } finally {
      this.mode = 'idle';
      this.render();
    }
  }

  /** Vuelve al último estado confirmado: GLB y asignaciones (RF-08). */
  private async discard(): Promise<void> {
    if (this.mode === 'busy') return;
    this.mode = 'busy';
    this.render();
    try {
      if (this.editor.dirty) this.editor.revert();
      if (this.previewing && this.confirmedUrl) await this.reload(this.confirmedUrl);
      this.previewing = false;
      this.issues = [];
    } catch (error) {
      this.app.notify(`No se pudo restaurar el modelo: ${String(error)}`, 'error');
    } finally {
      this.mode = 'idle';
      this.render();
    }
  }

  // --- Importar / exportar --------------------------------------------------------------------

  private exportPlan(): void {
    const blob = new Blob([JSON.stringify(this.plan(), null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = h('a', { href: url, download: 'materials.json' });
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private async importPlan(file: File): Promise<void> {
    try {
      const plan = JSON.parse(await file.text()) as Partial<MaterialPlan>;
      if (!Array.isArray(plan.assignments)) throw new Error('no tiene "assignments"');
      if (plan.geometryRevision && plan.geometryRevision !== this.geometryRevision) {
        this.app.notify(
          'El plan es de otra revisión geométrica: revisa las asignaciones antes de aplicarlo.',
          'warning',
        );
      }
      const known = plan.assignments.filter((a) => a.objectIds.every((id) => this.catalog.has(id)));
      this.editor.replace(plan.assignments);
      if (known.length < plan.assignments.length) {
        this.app.notify(
          `${plan.assignments.length - known.length} asignaciones citan objetos que no están en este modelo.`,
          'warning',
        );
      }
    } catch (error) {
      this.app.notify(`No se pudo importar el plan: ${String(error)}`, 'error');
    }
  }

  // --- Render ---------------------------------------------------------------------------------

  private statusText(): string {
    if (!this.app.model) return 'Abre un modelo para asignar materiales.';
    if (!this.geometryRevision || !this.catalog.size) {
      return 'Este GLB no tiene superficies por objeto: vuelve a convertirlo con esta versión.';
    }
    if (this.materialized && !this.job) {
      return 'Este GLB ya está materializado. Abre el model.glb base para editar sus materiales.';
    }
    if (!this.library)
      return 'Sin biblioteca: arranca extrude serve (cd pipeline && uv run extrude serve).';
    if (this.mode === 'busy') return 'Aplicando…';
    const confirmed = this.confirmedRevision
      ? `Confirmado ${this.confirmedRevision}`
      : 'Sin confirmar';
    if (this.previewing) return `Previsualizando · ${confirmed}`;
    return this.editor.dirty ? `${confirmed} · cambios pendientes` : confirmed;
  }

  render(): void {
    this.status.textContent = this.statusText();
    const usable =
      Boolean(this.app.model && this.library && this.geometryRevision && this.catalog.size) &&
      !(this.materialized && !this.job);
    const busy = this.mode === 'busy';
    this.fillSurfaces();
    this.renderObjects();
    this.renderAssignments();
    this.undoButton.disabled = !this.editor.canUndo || busy;
    this.redoButton.disabled = !this.editor.canRedo || busy;
    this.previewButton.disabled = !usable || busy || !this.editor.assignments.length;
    this.confirmButton.disabled = !usable || busy || (!this.editor.dirty && !this.previewing);
    this.discardButton.disabled = busy || (!this.editor.dirty && !this.previewing);
    this.downloadList.replaceChildren(
      ...this.downloads.map((d) =>
        h('a', {
          class: 'btn btn-ghost btn-sm',
          href: d.href,
          download: d.filename,
          textContent: d.label,
        }),
      ),
    );
  }
}
