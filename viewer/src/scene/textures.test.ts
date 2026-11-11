import { BoxGeometry, Group, Mesh, MeshStandardMaterial, Texture } from 'three';
import { describe, expect, it } from 'vitest';
import { sharpenTextures } from './textures';

describe('sharpenTextures', () => {
  it('aplica la anisotropía máxima una vez por textura compartida', () => {
    const map = new Texture();
    const shared = new MeshStandardMaterial({ map });
    const root = new Group();
    root.add(new Mesh(new BoxGeometry(), shared), new Mesh(new BoxGeometry(), [shared]));
    root.add(new Mesh(new BoxGeometry(), new MeshStandardMaterial()));
    const version = map.version;
    expect(sharpenTextures(root, 16)).toBe(1);
    expect(map.anisotropy).toBe(16);
    expect(map.version).toBe(version + 1);
    sharpenTextures(root, 16);
    expect(map.version).toBe(version + 1); // sin re-subir si ya estaba
  });
});
