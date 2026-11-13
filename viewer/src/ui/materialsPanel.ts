/**
 * Pestaña Materiales (Fases 2 y 3, docs/REQUISITOS_MATERIALIDAD_IA.md): descripción con IA
 * opcional (ver editSection.ts), asignación manual por objeto y superficie, previsualización,
 * deshacer y confirmación. Ambos caminos producen el mismo plan y usan el mismo motor.
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
import type { ProjectStatus } from '../materials/ai';
import type { ViewsTarget } from '../materials/viewerOps';
import {
  PlanEditor,
  type AiTrace,
  type Assignment,
  type MaterialPlan,
  type Omission,
} from '../materials/plan';
import { EditSection } from './editSection';
import { button, h, pickFile, section } from './dom';

/** Metadatos del plan que no son asignaciones: descripción, trazabilidad de IA y omisiones. */
interface PlanMeta {
  description?: string;
  ai?: AiTrace;
  omissions: Omission[];
}

const STORAGE_PREFIX = 'extrude.materials.';
const SUMMARY_LABELS = 3;

type Mode = 'idle' | 'busy';

export interface MaterialsPanelOptions {
  server: string;
  /** Vistas del visor (las herramientas de IA crean vistas y cambian el sol). */
  views: ViewsTarget;
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
  private meta: PlanMeta = { omissions: [] };
  private confirmedMeta: PlanMeta = { omissions: [] };
  private readonly ai: EditSection;
  private readonly pieceLabel = h('span', { class: 'field-label', textContent: 'Pieza (m)' });
  private readonly descriptionInfo = h('div', { class: 'ai-plan-description' });

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
    this.ai = new EditSection(
      {
        app,
        views: options.views,
        catalog: () => this.catalog,
        selected: () => this.selected,
        mark: (ids) => {
          ids.forEach((id) => this.selected.add(id));
          this.render();
        },
        usable: () => this.usable,
        manualDirty: () => this.editor.dirty,
        library: () => this.library,
        ensureJob: () => this.ensureJob(),
        showPreview: async (url) => {
          this.clearLayerColors();
          await this.reload(url);
          this.previewing = true;
          this.render();
        },
        discardPreview: async () => {
          if (this.confirmedUrl) await this.reload(this.confirmedUrl);
          this.previewing = false;
          this.render();
        },
        applyProject: (status, plan) => this.applyProject(status, plan),
      },
      options.server,
    );

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
      h('label', { class: 'field' }, this.pieceLabel, tile),
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
      this.descriptionInfo,
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
      this.ai.element,
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

  /** Revisión con materiales o ediciones confirmados (se perdería al reconvertir), o null. */
  get confirmedProjectRevision(): string | null {
    const revision = this.ai.revision;
    return revision && revision !== 'rev-000' ? revision : null;
  }

  /** El modelo actual procede de este trabajo del servidor (conversión desde el visor). */
  setJob(job: string | null, baseUrl: string | null): void {
    this.job = job;
    this.confirmedUrl = baseUrl;
    if (job) {
      void this.loadConfirmed(job);
      void this.ai.setJob(job);
    }
    this.render();
  }

  /** Estado del proyecto tras confirmar, deshacer o rehacer: modelo, plan y revisión. */
  private async applyProject(
    status: ProjectStatus,
    plan: MaterialPlan | null | undefined,
  ): Promise<void> {
    this.previewing = false;
    this.confirmedUrl = status.modelUrl;
    this.confirmedRevision = status.materialRevision;
    this.clearLayerColors();
    await this.reload(status.modelUrl);
    const current =
      plan === undefined && this.job ? await this.client.confirmedPlan(this.job) : plan;
    const confirmed = status.materialRevision ? (current ?? null) : null;
    this.confirmedMeta = {
      ...(confirmed?.description ? { description: confirmed.description } : {}),
      ...(confirmed?.ai ? { ai: confirmed.ai } : {}),
      omissions: confirmed?.omissions ?? [],
    };
    this.meta = { ...this.confirmedMeta };
    this.editor.setConfirmed(confirmed?.assignments ?? [], true);
    this.render();
  }

  // --- Modelo ---------------------------------------------------------------------------------

  private onModel(): void {
    if (this.reloading) {
      this.catalog = this.app.model
        ? buildCatalog(this.app.model.root)
        : new Map<string, CatalogObject>();
      // Una edición geométrica cambia la revisión (alturas, copias, posiciones)
      this.geometryRevision = this.app.model?.extras?.geometry_revision ?? this.geometryRevision;
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
    this.meta = { omissions: [] };
    this.confirmedMeta = { omissions: [] };
    this.ai.reset();
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
    if (model) void this.ai.loadProviders().then(() => this.render());
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
    this.confirmedMeta = {
      ...(plan.description ? { description: plan.description } : {}),
      ...(plan.ai ? { ai: plan.ai } : {}),
      omissions: plan.omissions ?? [],
    };
    if (!this.editor.dirty) this.meta = { ...this.confirmedMeta };
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
    // El campo edita el tamaño real de una pieza (ladrillo, losa); sin piezas, la repetición
    const piece = preset.pieceSizeM ?? preset.tileSizeM;
    this.pieceLabel.textContent = preset.pieceSizeM ? 'Pieza (m)' : 'Repetición (m)';
    this.tileWidth.value = String(Number(piece[0].toFixed(3)));
    this.tileHeight.value = String(Number(piece[1].toFixed(3)));
    this.rotationInput.value = '0';
  }

  private pieceText(materialId: string, tile: [number, number]): string {
    const pieces = this.preset(materialId)?.piecesPerTile;
    if (!pieces) return `repetición ${tile.map((v) => v.toFixed(2)).join('×')} m`;
    return `pieza ${(tile[0] / pieces[0]).toFixed(3)}×${(tile[1] / pieces[1]).toFixed(3)} m`;
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
    const pieces = preset.piecesPerTile ?? [1, 1];
    const tile: [number, number] = [
      Math.min(width * pieces[0], 100),
      Math.min(height * pieces[1], 100),
    ];
    if (
      Math.abs(tile[0] - preset.tileSizeM[0]) > 1e-3 ||
      Math.abs(tile[1] - preset.tileSizeM[1]) > 1e-3
    ) {
      parameters.tileSizeM = tile; // el plan guarda el mosaico; la pieza es tile / piezas
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
          a.parameters.tileSizeM ? this.pieceText(a.materialId, a.parameters.tileSizeM) : null,
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
    return this.editor.toPlan(
      {
        projectId: 'visor',
        geometryRevision: this.geometryRevision ?? '',
        materialRevision: this.confirmedRevision ?? 'mat-000',
        catalogVersion: this.library?.version ?? '',
        ...(this.meta.description ? { description: this.meta.description.slice(0, 10_000) } : {}),
        ...(this.meta.ai ? { ai: this.meta.ai } : {}),
      },
      this.meta.omissions.slice(0, 200),
    );
  }

  /** Hay cambios en descripción, trazabilidad u omisiones respecto a lo confirmado. */
  private get metaDirty(): boolean {
    return JSON.stringify(this.meta) !== JSON.stringify(this.confirmedMeta);
  }

  private get usable(): boolean {
    return (
      Boolean(this.app.model && this.library && this.geometryRevision && this.catalog.size) &&
      !(this.materialized && !this.job)
    );
  }

  /** RS-10: quitar el texto original del plan conservando asignaciones y trazabilidad. */
  private removeDescription(): void {
    if (!this.meta.description) return;
    const rest: PlanMeta = { ...this.meta };
    delete rest.description;
    this.meta = rest;
    this.app.notify(
      'La descripción se quitará del plan al confirmar; las asignaciones se conservan.',
      'info',
    );
    this.render();
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
      const result = await this.client.applyPlan(job, this.plan(), preview, this.ai.revision);
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
        void this.ai.refreshProject(); // confirmar a mano también crea una revisión
        this.confirmedMeta = { ...this.meta };
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
      this.meta = { ...this.confirmedMeta };
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
      this.meta = {
        ...(typeof plan.description === 'string' ? { description: plan.description } : {}),
        ...(plan.ai ? { ai: plan.ai } : {}),
        omissions: Array.isArray(plan.omissions) ? plan.omissions : [],
      };
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
    return this.editor.dirty || this.metaDirty ? `${confirmed} · cambios pendientes` : confirmed;
  }

  private renderDescription(): void {
    const { description, ai } = this.meta;
    if (!description && !ai) {
      this.descriptionInfo.replaceChildren();
      return;
    }
    const parts: (HTMLElement | null)[] = [
      ai
        ? h('p', {
            class: 'ai-note',
            textContent: `Propuesta revisada de ${ai.provider} (${ai.model}): ${ai.summary}`,
          })
        : null,
      description
        ? h(
            'div',
            { class: 'mat-row-actions' },
            h('span', {
              class: 'ai-note',
              textContent: 'El plan guarda tu descripción original.',
            }),
            button({
              label: 'Quitar descripción',
              variant: 'ghost',
              size: 'sm',
              onClick: () => this.removeDescription(),
            }),
          )
        : null,
    ];
    this.descriptionInfo.replaceChildren(...parts.filter((n): n is HTMLElement => n !== null));
  }

  render(): void {
    this.status.textContent = this.statusText();
    const usable = this.usable;
    const busy = this.mode === 'busy';
    const dirty = this.editor.dirty || this.metaDirty;
    this.app.materialPreview = this.previewing;
    this.fillSurfaces();
    this.renderObjects();
    this.renderAssignments();
    this.undoButton.disabled = !this.editor.canUndo || busy;
    this.redoButton.disabled = !this.editor.canRedo || busy;
    this.previewButton.disabled = !usable || busy || !this.editor.assignments.length;
    this.confirmButton.disabled = !usable || busy || (!dirty && !this.previewing);
    this.discardButton.disabled = busy || (!dirty && !this.previewing);
    this.renderDescription();
    this.ai.render();
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
