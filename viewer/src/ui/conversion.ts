import type { ConvertResponse, ConversionClient } from '../convert/client';

/**
 * Controles de conversión en la barra superior: YAML de configuración elegido (opcional) y
 * enlaces a los resultados de la última conversión (GLB, informe y configuración usada).
 */
export class ConversionBar {
  readonly element = document.createElement('span');
  configYaml: string | null = null;
  private readonly configLabel = document.createElement('span');
  private readonly links = document.createElement('span');

  constructor(private readonly client: ConversionClient) {
    this.element.className = 'conversion';
    const pick = document.createElement('button');
    pick.type = 'button';
    pick.textContent = 'Config YAML…';
    pick.title =
      'Configuración del proyecto para la próxima conversión (si no, se genera un borrador)';
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.yaml,.yml';
    input.hidden = true;
    pick.addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      input.value = '';
      if (file) void this.setConfig(file);
    });
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.textContent = '×';
    clear.title = 'Quitar configuración (se generará un borrador automático)';
    clear.addEventListener('click', () => this.clearConfig());
    this.element.append(pick, input, this.configLabel, clear, this.links);
    this.clearConfig();
  }

  async setConfig(file: File): Promise<void> {
    this.configYaml = await file.text();
    this.configLabel.textContent = file.name;
  }

  clearConfig(): void {
    this.configYaml = null;
    this.configLabel.textContent = 'config automática';
  }

  showResult(result: ConvertResponse, baseName: string): void {
    const make = (
      label: string,
      path: string | null,
      filename: string,
    ): HTMLAnchorElement | null => {
      if (!path) return null;
      const a = document.createElement('a');
      a.href = this.client.url(path);
      a.download = filename;
      a.textContent = label;
      return a;
    };
    this.links.replaceChildren(
      ...[
        make('GLB', result.files.glb, `${baseName}.glb`),
        make('Informe', result.files.report_md, `${baseName}_report.md`),
        make(
          result.config_generated ? 'Config generada' : 'Config usada',
          result.files.config,
          `${baseName}.yaml`,
        ),
      ].filter((a): a is HTMLAnchorElement => a !== null),
    );
  }
}
