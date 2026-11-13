import {
  BoxGeometry,
  BufferGeometry,
  DataTexture,
  Group,
  InstancedMesh,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshStandardMaterial,
  Scene,
} from 'three';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../app';
import type { LoadedModel } from './loader';
import { disposeObject } from './dispose';

function spyDispose(target: { dispose: () => void }) {
  return vi.spyOn(target, 'dispose');
}

function model(): { root: Group; geometry: BoxGeometry; material: MeshStandardMaterial } {
  const geometry = new BoxGeometry();
  const material = new MeshStandardMaterial();
  const root = new Group();
  root.add(new Mesh(geometry, material));
  return { root, geometry, material };
}

describe('disposeObject', () => {
  it('libera geometrías, materiales y texturas de mallas, líneas e instancias', () => {
    const map = new DataTexture();
    const material = new MeshStandardMaterial({ map });
    const shared = new BoxGeometry();
    const lineGeometry = new BufferGeometry();
    const lineMaterial = new LineBasicMaterial();
    const instanced = new InstancedMesh(new BoxGeometry(), material, 3);
    const root = new Group();
    root.add(new Mesh(shared, material), new Mesh(shared, [material, material]));
    root.add(new LineSegments(lineGeometry, lineMaterial), instanced);

    const spies = [shared, lineGeometry, instanced.geometry, lineMaterial, map].map(spyDispose);
    const materialSpy = spyDispose(material);
    const instancedSpy = spyDispose(instanced);
    disposeObject(root);

    for (const spy of spies) expect(spy).toHaveBeenCalledTimes(1);
    expect(materialSpy).toHaveBeenCalledTimes(1); // compartido: una sola vez
    expect(instancedSpy).toHaveBeenCalledTimes(1); // búferes de instancias
  });

  it('no libera las texturas excluidas (entorno de la escena)', () => {
    const env = new DataTexture();
    const root = new Group();
    root.add(new Mesh(new BoxGeometry(), new MeshStandardMaterial({ envMap: env })));
    const spy = spyDispose(env);
    disposeObject(root, new Set([env]));
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('App.releaseModel (usado por setModel y unload)', () => {
  it('quita el modelo previo de la escena y libera su GPU, pero no el entorno', () => {
    const previous = model();
    const env = new DataTexture();
    previous.material.envMap = env;
    const scene = new Scene();
    scene.environment = env;
    scene.add(previous.root);
    const host = {
      scene,
      model: {
        root: previous.root,
        name: 'a',
        extras: null,
        bytes: new ArrayBuffer(0),
      } as LoadedModel,
      measure: { clear: vi.fn() }, // los puntos medidos eran del modelo anterior
      highlight: { clear: vi.fn() }, // las cajas de una propuesta eran del modelo anterior
      layerHighlight: { clear: vi.fn() }, // las copias de la capa resaltada, también
    };
    const geometrySpy = spyDispose(previous.geometry);
    const materialSpy = spyDispose(previous.material);
    const envSpy = spyDispose(env);

    (App.prototype as unknown as { releaseModel(this: typeof host): void }).releaseModel.call(host);

    expect(geometrySpy).toHaveBeenCalledTimes(1);
    expect(materialSpy).toHaveBeenCalledTimes(1);
    expect(envSpy).not.toHaveBeenCalled();
    expect(scene.children).not.toContain(previous.root);
    expect(host.model).toBeNull();
    expect(host.measure.clear).toHaveBeenCalledOnce();
    expect(host.highlight.clear).toHaveBeenCalledOnce();
  });
});
