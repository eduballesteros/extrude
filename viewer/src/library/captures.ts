/**
 * Capturas de vistas guardadas y operaciones sobre la biblioteca que necesitan el visor:
 * capturar en HD o 4K, descargar, ampliar y guardar resultados de IA con su procedencia.
 */
import type { App } from '../app';
import { download } from '../export/download';
import type { ViewManager } from '../views/viewManager';
import type { SceneView } from '../views/viewState';
import { inspectImage, resizeImage } from './bitmap';
import type { GenerationJob } from './generation';
import {
  type CapturePreset,
  captureSize,
  imageFilename,
  type ImageLibrary,
  type LibraryImage,
  type NewImage,
  PRESET_BOX,
} from './images';

export class MissingFileError extends Error {
  constructor() {
    super('El archivo de esta imagen ya no está en el navegador. Quítala de la biblioteca.');
  }
}

export class Captures {
  constructor(
    private readonly app: App,
    private readonly views: ViewManager,
    readonly library: ImageLibrary,
  ) {
    // La biblioteca es del proyecto (nombre del modelo), igual que las vistas
    app.on('model', () => void library.open(app.model?.name ?? null));
  }

  get project(): string | null {
    return this.app.model?.name ?? null;
  }

  /** Dimensiones finales de la captura de esa vista (conserva su proporción). */
  size(view: SceneView, preset: CapturePreset): { width: number; height: number } {
    return captureSize(view.render.width, view.render.height, preset);
  }

  /** Renderiza la vista a HD o 4K y la guarda en la biblioteca como registro nuevo. */
  async capture(viewId: string, preset: CapturePreset): Promise<LibraryImage> {
    const project = this.project;
    const view = this.views.views.find((v) => v.id === viewId);
    if (!project || !view) throw new Error('Esa vista ya no existe.');
    const { width, height } = this.size(view, preset);
    const blob = await this.views.captureView(viewId, width, height);
    const facts = await inspectImage(blob);
    const date = new Date();
    return this.library.add(
      {
        project,
        viewId,
        viewName: view.name,
        kind: 'capture',
        width: facts.width,
        height: facts.height,
        mime: 'image/png',
        thumbnail: facts.thumbnail,
        preset,
        modelRevision:
          this.app.model?.extras?.material_revision ??
          this.app.model?.extras?.geometry_revision ??
          null,
        filename: imageFilename({
          project,
          view: view.name,
          kind: 'capture',
          width: facts.width,
          height: facts.height,
          mime: 'image/png',
          date,
        }),
      },
      blob,
      date,
    );
  }

  /** Guarda en la biblioteca la imagen que devolvió el proveedor, tal cual llegó. */
  async storeGenerated(options: {
    view: SceneView;
    blob: Blob;
    job: GenerationJob;
    prompt: string;
    providerName: string;
    sources: LibraryImage[];
  }): Promise<LibraryImage> {
    const { view, blob, job, prompt, providerName, sources } = options;
    const project = this.project;
    if (!project) throw new Error('No hay ningún proyecto abierto.');
    const facts = await inspectImage(blob); // dimensiones reales del archivo recibido
    const mime = job.result?.mime ?? (blob.type || 'image/png');
    const date = new Date();
    const simulated = job.result?.simulated ?? false;
    return this.library.add(
      {
        project,
        viewId: view.id,
        viewName: view.name,
        kind: 'ai',
        width: facts.width,
        height: facts.height,
        mime,
        thumbnail: facts.thumbnail,
        prompt,
        provider: job.provider,
        providerName,
        model: job.result?.model ?? job.model,
        requested: {
          label: job.requested.label,
          width: job.requested.width,
          height: job.requested.height,
        },
        sourceImageIds: sources.map((source) => source.id),
        simulated,
        matchesRequest: job.result?.matchesRequest ?? true,
        filename: imageFilename({
          project,
          view: view.name,
          kind: 'ai',
          width: facts.width,
          height: facts.height,
          mime,
          date,
          simulated,
        }),
      },
      blob,
      date,
    );
  }

  async blob(image: LibraryImage): Promise<Blob> {
    const blob = await this.library.blob(image.id);
    if (!blob) throw new MissingFileError();
    return blob;
  }

  async download(image: LibraryImage): Promise<void> {
    download(await this.blob(image), image.filename);
  }

  /** Tamaño al que se ampliaría una imagen para llegar a 4K con su proporción; null si ya llega. */
  upscaleSize(image: LibraryImage): { width: number; height: number } | null {
    const size = captureSize(image.width, image.height, '4K');
    const box = PRESET_BOX['4K'];
    if (image.width >= size.width || image.width >= box.width) return null;
    return size;
  }

  /**
   * Amplía por interpolación un resultado de IA hasta 4K y lo guarda APARTE, marcado como
   * ampliado: el original recibido se conserva y nunca se presenta la ampliación como nativa.
   */
  async upscale(image: LibraryImage): Promise<LibraryImage> {
    const size = this.upscaleSize(image);
    if (!size) throw new Error('La imagen ya tiene resolución 4K.');
    const blob = await resizeImage(await this.blob(image), size.width, size.height);
    const facts = await inspectImage(blob);
    const date = new Date();
    const rest: Partial<LibraryImage> & NewImage = { ...image };
    delete rest.id;
    delete rest.createdAt;
    delete rest.bytes;
    return this.library.add(
      {
        ...rest,
        width: facts.width,
        height: facts.height,
        mime: 'image/png',
        thumbnail: facts.thumbnail,
        upscaledFrom: image.upscaledFrom ?? image.id,
        filename: imageFilename({
          project: image.project,
          view: image.viewName,
          kind: 'ai',
          width: facts.width,
          height: facts.height,
          mime: 'image/png',
          date,
          upscaled: true,
          simulated: image.simulated ?? false,
        }),
      },
      blob,
      date,
    );
  }
}
