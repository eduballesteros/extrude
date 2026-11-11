import type { ConvertResponse } from '../convert/client';
import { summarizeQuality } from '../convert/report';
import { h } from './dom';

/** Resumen persistente y plegable, ligado al modelo convertido actualmente abierto. */
export class QualitySummary {
  readonly element = h('details', { class: 'quality-summary', hidden: true });

  clear(): void {
    this.element.hidden = true;
    this.element.replaceChildren();
  }

  show(result: ConvertResponse, url: (path: string) => string): void {
    const quality = summarizeQuality(result.report);
    const rows = h('dl', { class: 'kv' });
    const row = (label: string, value: string | number): void => {
      rows.append(h('dt', { textContent: label }), h('dd', { textContent: String(value) }));
    };
    if (quality.available) {
      row('Alturas por defecto', quality.defaultHeights);
      row('Alturas ambiguas', quality.ambiguous);
      row('Sin altura · descartados', quality.dropped);
      row('Rótulos junto a fachada', quality.nearby);
      if (quality.groundAvailable) {
        row('Sólidos comprobados', quality.checked);
        row('Flotan completamente', quality.floating);
        row('Enterrados completamente', quality.buried);
        row('Intersecan la ladera', quality.intersecting);
        row('Enterrados parcialmente', quality.partiallyBuried);
        if (quality.coverageAvailable) row('Huellas fuera del terreno TIN', quality.extrapolated);
        if (quality.foundations) {
          row('Edificios con zócalo automático', quality.foundations);
          row('Zócalo máximo', `${quality.maxFoundationHeight.toFixed(2)} m`);
        }
      }
    }
    const links = h('div', { class: 'quality-links' });
    for (const [label, path] of [
      ['Informe completo', result.files.report_md],
      [
        result.config_generated ? 'Configuración generada' : 'Configuración usada',
        result.files.config,
      ],
    ] as const) {
      try {
        const href = new URL(url(path));
        if (!['http:', 'https:'].includes(href.protocol)) continue;
        links.append(
          h('a', {
            href: href.href,
            textContent: label,
            target: '_blank',
            rel: 'noopener noreferrer',
          }),
        );
      } catch {
        // Un enlace incompleto de un servidor anterior no impide abrir el modelo.
      }
    }
    const notes = h('div', { class: 'quality-notes' });
    if (quality.extrapolated) {
      notes.append(
        h('p', {
          textContent:
            'Hay huellas sin cobertura completa del TIN: su apoyo exterior se estima por proximidad. Revisa o amplía la topografía aunque no aparezcan flotantes.',
        }),
      );
    }
    if (quality.foundations) {
      notes.append(
        h('p', {
          textContent:
            'Los edificios adaptados mantienen la cubierta horizontal y un zócalo hasta el terreno. Es una aproximación automática: revisa los zócalos altos y las cotas de proyecto en el informe.',
        }),
      );
    }
    if (!quality.available) {
      notes.append(
        h('p', {
          textContent:
            'Este servidor no incluye los recuentos de calidad. Consulta el informe completo.',
        }),
      );
    } else if (!quality.groundAvailable) {
      notes.append(
        h('p', { textContent: 'Apoyo sin comprobar: no hay datos de terreno en el informe.' }),
      );
    }
    if (quality.intersecting) {
      notes.append(
        h('p', {
          textContent:
            'Las intersecciones con ladera pueden deberse a una base horizontal sobre terreno inclinado. Consulta las cotas y los identificadores por objeto en el informe completo.',
        }),
      );
    }
    if (quality.partiallyBuried) {
      notes.append(
        h('p', {
          textContent:
            'Revisa los objetos parcialmente enterrados: el terreno supera su cubierta en parte de la huella. Comprueba la altura asignada, la cota de base y las curvas del plano; el recuento por sí solo no permite atribuir la causa al archivo.',
        }),
      );
    }
    if (result.config_generated) {
      notes.append(
        h('p', {
          textContent:
            'Se ha usado una configuración automática. Revisa el YAML antes de dar el modelo por válido.',
        }),
      );
    }
    if (result.report.warnings.length) {
      const warnings = h('ul');
      for (const warning of result.report.warnings.slice(0, 5)) {
        warnings.append(h('li', { textContent: warning }));
      }
      notes.append(warnings);
      if (result.report.warnings.length > 5)
        notes.append(h('p', { textContent: 'Más avisos en el informe completo.' }));
    }
    this.element.replaceChildren(
      h('summary', {
        textContent: `Calidad de la conversión · ${result.report.warnings.length} ${result.report.warnings.length === 1 ? 'aviso' : 'avisos'}`,
      }),
      h('div', { class: 'quality-body' }, links, rows, notes),
    );
    this.element.open = true;
    this.element.hidden = false;
  }
}
