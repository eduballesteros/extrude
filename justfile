# CI local de Extrude. Requiere: uv, node/npm, just.
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

default:
    @just --list

# Instala dependencias del pipeline, del visor y de las herramientas Node
setup: setup-pipeline setup-viewer setup-tools

[working-directory: 'pipeline']
setup-pipeline:
    uv sync

[working-directory: 'viewer']
setup-viewer:
    npm install --no-fund --no-audit

[working-directory: 'tools']
setup-tools:
    npm install --no-fund --no-audit

# Ejecuta todos los tests
test: test-pipeline test-viewer

[working-directory: 'pipeline']
test-pipeline:
    uv run pytest

[working-directory: 'viewer']
test-viewer:
    npm test

# Lint, formato y tipado
lint: lint-pipeline lint-viewer

[working-directory: 'pipeline']
lint-pipeline:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy

[working-directory: 'viewer']
lint-viewer:
    npm run lint

# Aplica formato automático
[working-directory: 'pipeline']
fmt-pipeline:
    uv run ruff format .
    uv run ruff check --fix .

[working-directory: 'viewer']
fmt-viewer:
    npm run format

fmt: fmt-pipeline fmt-viewer

# Comprueba los conversores DWG→DXF disponibles
[working-directory: 'pipeline']
doctor:
    uv run extrude doctor

# Regenera los DXF sintéticos de samples/
[working-directory: 'pipeline']
samples:
    uv run python tests/sample_dxf.py ../samples

# Inspecciona un DWG/DXF (ruta relativa a la raíz del repo)
inspect file:
    cd pipeline; uv run extrude inspect ../{{file}}

# Convierte las muestras con sus configs (samples/config) a out/<muestra>/
[working-directory: 'pipeline']
convert-samples:
    uv run extrude convert ../samples/block_2d.dxf -c ../samples/config/block_2d.yaml -o ../out/block_2d
    uv run extrude convert ../samples/block_2d_utm.dxf -c ../samples/config/block_2d.yaml -o ../out/block_2d_utm
    uv run extrude convert ../samples/nested_blocks.dxf -c ../samples/config/nested_blocks.yaml -o ../out/nested_blocks
    uv run extrude convert ../samples/faces_3d.dxf -c ../samples/config/faces_3d.yaml -o ../out/faces_3d
    uv run extrude convert ../samples/dirty.dxf -c ../samples/config/dirty.yaml -o ../out/dirty

# Valida GLB con gltf-validator (Khronos)
validate +files:
    node tools/validate.mjs {{files}}

# Prueba de rendimiento: ciudad sintética de N edificios
[working-directory: 'pipeline']
bench n='50000':
    uv run python tests/sample_dxf.py ../out/bench {{n}}
    uv run extrude convert ../out/bench/city_{{n}}.dxf -c ../samples/config/city.yaml -o ../out/bench/city_{{n}}

# Arranca el visor en modo desarrollo
[working-directory: 'viewer']
dev:
    npm run dev

# Todo lo que debe pasar antes de un commit
check: lint test
