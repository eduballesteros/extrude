import { describe, expect, it } from 'vitest';
import { MAX_HISTORY, PlanEditor, type Assignment } from './plan';

function brick(ids: string[], surface: Assignment['surface'] = 'facade_exterior'): Assignment {
  return {
    objectIds: ids,
    surface,
    materialId: 'brick_light',
    materialVersion: '1',
    parameters: {},
    origin: 'manual',
  };
}

describe('PlanEditor', () => {
  it('la asignación nueva prevalece para los mismos objeto + superficie', () => {
    const plan = new PlanEditor();
    plan.add(brick(['a', 'b', 'c']));
    plan.add({ ...brick(['b']), materialId: 'concrete_exposed' });
    plan.add(brick(['a'], 'roof'));
    expect(plan.assignments.map((a) => [a.materialId, a.surface, a.objectIds])).toEqual([
      ['brick_light', 'facade_exterior', ['a', 'c']],
      ['concrete_exposed', 'facade_exterior', ['b']],
      ['brick_light', 'roof', ['a']],
    ]);
    // Si una asignación se queda sin objetos, desaparece
    plan.add({ ...brick(['a', 'c']), materialId: 'render_white' });
    expect(plan.assignments.map((a) => a.materialId)).toEqual([
      'concrete_exposed',
      'brick_light',
      'render_white',
    ]);
  });

  it('deshace y rehace, y una edición nueva borra el rehacer', () => {
    const plan = new PlanEditor();
    plan.add(brick(['a']));
    plan.add(brick(['b'], 'roof'));
    plan.undo();
    expect(plan.assignments).toHaveLength(1);
    plan.redo();
    expect(plan.assignments).toHaveLength(2);
    plan.undo();
    plan.remove(0);
    expect(plan.canRedo).toBe(false);
    expect(plan.assignments).toHaveLength(0);
  });

  it('conserva al menos 20 operaciones de historial', () => {
    const plan = new PlanEditor();
    for (let i = 0; i < MAX_HISTORY + 5; i++) plan.add(brick([`o${i}`], 'roof'));
    let steps = 0;
    while (plan.canUndo) {
      plan.undo();
      steps++;
    }
    expect(steps).toBe(MAX_HISTORY);
    expect(steps).toBeGreaterThanOrEqual(20);
  });

  it('sabe si hay cambios sin confirmar y vuelve al estado confirmado', () => {
    const plan = new PlanEditor();
    plan.setConfirmed([brick(['a'])], true);
    expect(plan.dirty).toBe(false);
    plan.add(brick(['b'], 'roof'));
    expect(plan.dirty).toBe(true);
    plan.revert();
    expect(plan.dirty).toBe(false);
    expect(plan.assignments).toHaveLength(1);
  });

  it('las copias internas no se comparten con quien llama', () => {
    const plan = new PlanEditor();
    const a = brick(['a']);
    plan.add(a);
    a.objectIds.push('x');
    expect(plan.assignments[0]!.objectIds).toEqual(['a']);
  });
});
