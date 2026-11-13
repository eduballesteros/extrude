"""Verifica copias/restauración y carga de secretos sin Docker ni proveedores."""

import io
import json
import runpy
import tarfile
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def empty_data(root: Path) -> Path:
    for name in ("accounts", "jobs"):
        (root / name).mkdir(parents=True)
    return root


def test_backup_roundtrip_preserves_accounts_projects_and_revisions(tmp_path: Path) -> None:
    maintenance = runpy.run_path(str(DEPLOY / "maintenance.py"))
    source = empty_data(tmp_path / "source")
    (source / "accounts/users.json").write_text('{"users": {}}')
    project = source / "jobs" / ("a" * 32)
    project.mkdir()
    (project / "access.json").write_text('{"owner":"ana"}')
    (project / "revision.glb").write_bytes(b"glTF\x00revision")
    stream = io.BytesIO()
    maintenance["backup"](source, stream)
    stream.seek(0)
    destination = empty_data(tmp_path / "destination")
    maintenance["restore"](destination, stream)
    for original in source.rglob("*"):
        if original.is_file():
            assert (
                destination / original.relative_to(source)
            ).read_bytes() == original.read_bytes()
    stream.seek(0)
    with pytest.raises(SystemExit, match="volúmenes vacíos"):
        maintenance["restore"](destination, stream)


@pytest.mark.parametrize("name", ["accounts/../../escape", "/outside", "private/key.json"])
def test_restore_rejects_paths_outside_data(tmp_path: Path, name: str) -> None:
    maintenance = runpy.run_path(str(DEPLOY / "maintenance.py"))
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        entry = tarfile.TarInfo(name)
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))
    stream.seek(0)
    with pytest.raises((SystemExit, tarfile.FilterError)):
        maintenance["restore"](empty_data(tmp_path / "data"), stream)
    assert not (tmp_path / "escape").exists()


def test_secrets_only_load_allowed_provider_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime = runpy.run_path(str(DEPLOY / "runtime.py"))
    secret = tmp_path / "providers.json"
    # Valor sintético de test, nunca se hace una llamada de red.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    secret.write_text(json.dumps({"OPENAI_API_KEY": "test-only"}))
    runtime["load_secrets"](secret)
    import os

    assert os.environ["OPENAI_API_KEY"] == "test-only"
    monkeypatch.delenv("OPENAI_API_KEY")
    for invalid in ({"PATH": "injected"}, {"OPENAI_API_KEY": ""}, []):
        secret.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            runtime["load_secrets"](secret)
