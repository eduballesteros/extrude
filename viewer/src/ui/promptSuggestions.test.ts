import { describe, expect, it } from 'vitest';
import {
  appendPhrase,
  applySuggestion,
  prependPhrase,
  promptHas,
  SUGGESTION_CATEGORIES,
} from './promptSuggestions';

const role = SUGGESTION_CATEGORIES.find((c) => c.id === 'rol')!;
const people = SUGGESTION_CATEGORIES.find((c) => c.id === 'gente')!;

describe('frases opcionales del prompt', () => {
  it('hay muchas categorías y frases, sin repetir', () => {
    const phrases = SUGGESTION_CATEGORIES.flatMap((c) => c.phrases);
    expect(SUGGESTION_CATEGORIES.length).toBeGreaterThanOrEqual(10);
    expect(phrases.length).toBeGreaterThanOrEqual(100);
    expect(new Set(phrases.map((p) => p.toLowerCase())).size).toBe(phrases.length);
    expect(new Set(SUGGESTION_CATEGORIES.map((c) => c.id)).size).toBe(SUGGESTION_CATEGORIES.length);
    for (const phrase of phrases) expect(phrase.trim()).toBe(phrase);
    expect(people.phrases).toContain('gente corriendo por el paseo');
  });

  it('las frases normales se añaden al final: con coma, o como frase nueva tras un punto', () => {
    expect(appendPhrase('', 'cielo azul despejado')).toBe('cielo azul despejado');
    expect(appendPhrase('Render del barrio', 'cielo azul despejado')).toBe(
      'Render del barrio, cielo azul despejado',
    );
    expect(appendPhrase('Render del barrio.', 'cielo azul despejado')).toBe(
      'Render del barrio. Cielo azul despejado',
    );
  });

  it('el rol abre el prompt y sustituye al anterior en vez de acumularse', () => {
    const [first, second] = role.phrases as [string, string];
    const once = applySuggestion('render del barrio', role, first);
    expect(once).toBe(`${first}. Render del barrio`);
    expect(applySuggestion(once, role, second)).toBe(`${second}. Render del barrio`);
    expect(prependPhrase('', first, role.phrases)).toBe(`${first}.`);
  });

  it('el rol sigue reconociéndose aunque después se añadan más frases', () => {
    const [first, second] = role.phrases as [string, string];
    let prompt = applySuggestion('', role, first);
    prompt = applySuggestion(prompt, people, 'gente corriendo por el paseo');
    expect(prompt).toBe(`${first}. Gente corriendo por el paseo`);
    prompt = applySuggestion(prompt, role, second);
    expect(prompt).toBe(`${second}. Gente corriendo por el paseo`);
  });

  it('detecta frases ya incluidas sin distinguir mayúsculas', () => {
    expect(
      promptHas('Vista con GENTE CORRIENDO POR EL PASEO', 'gente corriendo por el paseo'),
    ).toBe(true);
    expect(promptHas('Vista aérea', 'gente corriendo por el paseo')).toBe(false);
  });
});
