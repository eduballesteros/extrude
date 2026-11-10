import fs from 'fs';

// Isometric unit vectors:
const cos30 = Math.cos(Math.PI / 6); // ~0.8660
const sin30 = Math.sin(Math.PI / 6); // 0.5

/**
 * Projects 3D (x, y, z) to 2D (px, py):
 * x: right-down (along isometric 30°)
 * y: left-down (along isometric 150°)
 * z: straight up (+Z)
 */
function iso(x, y, z, ox = 32, oy = 32) {
  const px = ox + (x - y) * cos30;
  const py = oy + (x + y) * sin30 - z;
  return `${px.toFixed(1)},${py.toFixed(1)}`;
}

console.log('Projector ready');
