# CI local de Extrude. Requiere: uv, node/npm, just.
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

default:
    @just --list

# Instala dependencias del pipeline y del visor
setup: setup-pipeline setup-viewer

[working-directory: 'pipeline']
setup-pipeline:
    uv sync

[working-directory: 'viewer']
setup-viewer:
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

# Arranca el visor en modo desarrollo
[working-directory: 'viewer']
dev:
    npm run dev

# Todo lo que debe pasar antes de un commit
check: lint test
